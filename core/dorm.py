"""Dorm groups: invite codes, equal splits, repayments, alerts."""
import secrets
from decimal import Decimal

from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .budget import ZERO, split_equally
from .models import Alert, DormGroup, Expense, Membership, Repayment, Share, SharedExpense

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I


def new_invite_code():
    while True:
        raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(7))
        code = f"{raw[:3]}-{raw[3:]}"
        if not DormGroup.objects.filter(invite_code=code).exists():
            return code


def group_of(user):
    membership = Membership.objects.filter(user=user).select_related("group").first()
    return membership.group if membership else None


def first_name(user):
    return user.first_name or user.get_username().split("@")[0]


def notify(user, kind, message):
    """In-app alert, plus email when the student turned that on."""
    profile = user.profile
    wanted = {
        "over": profile.alert_over_limit,
        "runway": profile.alert_runway,
        "bill": profile.alert_bill_due,
        "shared": profile.alert_shared,
    }.get(kind, True)
    if not wanted:
        return
    Alert.objects.create(user=user, kind=kind, message=message)
    if profile.email_alerts and user.email:
        send_mail("DormWise", message, None, [user.email], fail_silently=True)


@transaction.atomic
def create_group(user, name):
    group = DormGroup.objects.create(name=name, invite_code=new_invite_code(), creator=user)
    Membership.objects.create(group=group, user=user)
    return group


@transaction.atomic
def split_bill(group, payer, title, total, members, date=None):
    """Equal split among `members`. The payer absorbs any leftover centavo.

    The payer's own share is logged as their expense. Everyone else's share
    is a receivable for the payer and an obligation for them.
    """
    date = date or timezone.localdate()
    share, leftover = split_equally(total, len(members))
    expense = SharedExpense.objects.create(group=group, title=title, total=total, paid_by=payer, date=date)
    for member in members:
        amount = share + leftover if member == payer else share
        Share.objects.create(shared_expense=expense, member=member, amount=amount)
        if member == payer:
            Expense.objects.create(user=payer, amount=amount, category="other", note=title,
                                   date=date, shared_expense=expense)
        else:
            notify(member, "shared", f"{first_name(payer)} added {title}. Your share is ₱{share:,.2f}.")
    return expense


def expense_status(expense, viewer):
    """Status line for one shared expense, as `viewer` sees it."""
    owing = [s for s in expense.shares.all() if s.member_id != expense.paid_by_id]
    paid, waiting, unpaid = [], [], []
    for share in owing:
        confirmed = sum((r.amount for r in share.repayments.all() if r.status == Repayment.CONFIRMED), ZERO)
        pending = sum((r.amount for r in share.repayments.all() if r.status == Repayment.PENDING), ZERO)
        if confirmed >= share.amount:
            paid.append(share)
        elif confirmed + pending >= share.amount:
            waiting.append(share)
        else:
            unpaid.append(share)

    if not owing or len(paid) == len(owing):
        label = "Settled"
    elif paid or waiting:
        label = "Partly settled"
    else:
        label = "Unpaid"

    def who(share):
        return "You" if share.member_id == viewer.id else first_name(share.member)

    bits = []
    if label == "Settled":
        bits.append("Everyone paid.")
    else:
        if paid:
            bits.append(", ".join(who(s) for s in paid) + " paid.")
        for share in waiting:
            verb = "sent yours" if share.member_id == viewer.id else "sent theirs"
            bits.append(f"{who(share)} {verb}, waiting for confirmation.")
        for share in unpaid:
            if share.member_id == viewer.id:
                bits.append(f"Your ₱{share.amount:,.2f} is still unpaid.")
            else:
                bits.append(f"{who(share)} still owes ₱{share.amount:,.2f}.")

    my_share = next((s for s in unpaid if s.member_id == viewer.id), None)
    each = owing[0].amount if owing else expense.total
    return {
        "label": label,
        "detail": " ".join(bits),
        "each": each,
        "my_unpaid_share": my_share,
        "can_delete": expense.paid_by_id == viewer.id
        and not Repayment.objects.filter(share__shared_expense=expense).exists(),
    }


def unsettled_total(user, group):
    """Money still moving between `user` and the group, in either direction."""
    owes = ZERO
    for share in Share.objects.filter(shared_expense__group=group).select_related("shared_expense"):
        if share.member_id == share.shared_expense.paid_by_id:
            continue
        if user.id not in (share.member_id, share.shared_expense.paid_by_id):
            continue
        confirmed = share.repayments.filter(status=Repayment.CONFIRMED).aggregate(t=Sum("amount"))["t"] or ZERO
        owes += max(share.amount - confirmed, ZERO)
    return owes


def record_repayment(share, today=None):
    """The debtor says they paid. The receiver still has to confirm."""
    today = today or timezone.localdate()
    paid = share.repayments.aggregate(t=Sum("amount"))["t"] or ZERO
    outstanding = share.amount - paid
    if outstanding <= ZERO:
        return None
    repayment = Repayment.objects.create(share=share, amount=outstanding, recorded_on=today)
    expense = share.shared_expense
    notify(expense.paid_by, "shared",
           f"{first_name(share.member)} says they sent ₱{outstanding:,.2f} for {expense.title}. Confirm it in Bills.")
    return repayment


def confirm_repayment(repayment, today=None):
    repayment.status = Repayment.CONFIRMED
    repayment.confirmed_on = today or timezone.localdate()
    repayment.save(update_fields=["status", "confirmed_on"])

import calendar
from collections import OrderedDict
from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import budget as money
from . import dorm
from .forms import (AlertsForm, BillForm, BudgetingForm, ExpenseForm, GroupForm, IncomeForm, JoinForm,
                    LoginForm, ProfileForm, ScheduleForm, SetupForm, SignupForm, SplitForm)
from .models import (Alert, AllowancePeriod, Bill, DayClose, DormGroup, Expense, Income, Membership,
                     Repayment, Share, SharedExpense)

ZERO = Decimal("0")


# ---------- Helpers ----------

def _budget_or_setup(request):
    """The student's budget, or None when they still need allowance setup."""
    return money.build(request.user)


def _next_month(day):
    year, month = (day.year + 1, 1) if day.month == 12 else (day.year, day.month + 1)
    return day.replace(year=year, month=month, day=min(day.day, calendar.monthrange(year, month)[1]))


def _due_tag(bill, today):
    days = (bill.due_date - today).days
    if days < 0:
        return {"text": "Overdue", "urgent": True}
    if days == 0:
        return {"text": "Due today", "urgent": True}
    if days <= 2:
        return {"text": f"Due in {days} day{'s' if days != 1 else ''}", "urgent": True}
    return {"text": "Reserved", "urgent": False}


def _coming_up(budget):
    """Bills and roommate debts that are set aside, soonest first."""
    rows = [{"when": bill.due_date.strftime("%b %-d"), "title": bill.title, "amount": bill.amount,
             "sort": bill.due_date} for bill in budget.bills_before]
    rows += [{"when": "Owed now", "title": f"Pay {dorm.first_name(row['to'])}, {row['share'].shared_expense.title}",
              "amount": row["amount"], "sort": budget.today} for row in budget.i_owe]
    return sorted(rows, key=lambda row: row["sort"])


# ---------- Public pages ----------

def landing(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    return render(request, "core/landing.html")


def account(request, tab="signup"):
    """Sign up and log in share one page, as tabs."""
    if request.user.is_authenticated:
        return redirect("dashboard")
    signup_form, login_form = SignupForm(prefix="su"), LoginForm(prefix="li")
    if request.method == "POST":
        tab = request.POST.get("tab", tab)
        if tab == "login":
            login_form = LoginForm(request.POST, prefix="li")
            if login_form.is_valid():
                auth_login(request, login_form.user)
                return redirect(request.GET.get("next") or "dashboard")
        else:
            signup_form = SignupForm(request.POST, prefix="su")
            if signup_form.is_valid():
                auth_login(request, signup_form.save())
                return redirect("setup")
    return render(request, "core/account.html", {"tab": tab, "signup_form": signup_form, "login_form": login_form})


@require_POST
def logout(request):
    auth_logout(request)
    return redirect("landing")


# ---------- Allowance setup ----------

@login_required
def setup(request):
    today = timezone.localdate()
    profile = request.user.profile
    previous = money.current_period(request.user)
    unpaid = Bill.objects.filter(user=request.user, paid_on__isnull=True)
    initial = {
        "amount": previous.amount if previous else None,
        "days": previous.total_days if previous else 10,
        "frequency": previous.frequency if previous else "custom",
        "savings": Decimal("0"),
        "floor": profile.essentials_floor,
    }
    form = SetupForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        next_on = today + timedelta(days=data["days"])
        # Optional bills typed into the setup page.
        rows = zip(request.POST.getlist("bill_title"), request.POST.getlist("bill_amount"),
                   request.POST.getlist("bill_due"))
        for title, amount, due in rows:
            bill_form = BillForm({"title": title, "amount": amount, "due_date": due})
            if title.strip() and bill_form.is_valid():
                bill = bill_form.save(commit=False)
                bill.user = request.user
                bill.save()
        AllowancePeriod.objects.create(
            user=request.user, amount=data["amount"], received_on=today, next_allowance_on=next_on,
            frequency=data["frequency"], savings_amount=data["savings"] or ZERO, rolled_over_on=today,
        )
        profile.essentials_floor = data["floor"]
        profile.save(update_fields=["essentials_floor"])
        messages.success(request, "Allowance set. Here is what you can spend today.")
        return redirect("dashboard")

    owed = sum((row["amount"] for row in money.what_i_owe(request.user)), ZERO)
    return render(request, "core/setup.html", {
        "form": form, "today": today, "bills": unpaid, "owed": owed,
        "has_period": previous is not None,
    })


# ---------- Dashboard ----------

@login_required
def dashboard(request):
    budget = _budget_or_setup(request)
    if budget is None:
        return redirect("setup")
    preview = budget.active_preview
    mood = "oops" if budget.needs_plan else "happy"
    if budget.needs_plan:
        line = "How do you want to bounce back?"
    elif budget.incident:
        line = "Plan set. Barya will keep you on it."
    elif budget.shortfall:
        line = "Money is tight. Check your bills and savings."
        mood = "oops"
    else:
        line = "You're on track. Keep it up."
    return render(request, "core/dashboard.html", {
        "budget": budget, "preview": preview, "mood": mood, "barya_line": line,
        "coming_up": _coming_up(budget), "group": dorm.group_of(request.user),
        "due_soon": [bill for bill in budget.bills_before if (bill.due_date - budget.today).days <= 2],
        "first_name": dorm.first_name(request.user),
    })


@login_required
@require_POST
def choose_plan(request):
    budget = _budget_or_setup(request)
    plan = request.POST.get("plan")
    if budget and budget.incident and plan in ("even", "fast"):
        chosen = money.choose_plan(budget.period, budget.incident, plan, budget.floor)
        if chosen != plan:
            messages.warning(request, "Fix it by Friday would dip under your essentials floor, so the "
                                      "overspend is spread out instead.")
        else:
            messages.success(request, "Plan set. Your daily limits are updated.")
    return redirect(request.POST.get("next") or "dashboard")


@login_required
@require_POST
def dismiss_alert(request, pk):
    Alert.objects.filter(pk=pk, user=request.user).update(read_at=timezone.now())
    return redirect(request.POST.get("next") or "dashboard")


# ---------- Expense tracker ----------

def _after_expense_saved(request, was_over):
    """Logging an expense that tips today over its limit opens recovery."""
    budget = money.build(request.user)
    if budget and budget.over_today > 0 and not was_over:
        dorm.notify(request.user, "over", f"Today went ₱{budget.over_today:,.2f} over your limit.")
        if budget.shortfall:
            dorm.notify(request.user, "runway", "Your money may not last until your next allowance.")
        return redirect("recovery")
    return redirect("expenses")


@login_required
def expenses(request):
    budget = _budget_or_setup(request)
    if budget is None:
        return redirect("setup")
    form = ExpenseForm(request.POST or None, initial={"date": budget.today, "category": "food"})
    if request.method == "POST" and form.is_valid():
        was_over = budget.over_today > 0
        expense = form.save(commit=False)
        expense.user = request.user
        expense.save()
        messages.success(request, f"Saved ₱{expense.amount:,.2f} for {expense.title}.")
        return _after_expense_saved(request, was_over)

    category = request.GET.get("cat", "")
    rows = Expense.objects.filter(user=request.user, date__gte=budget.period.received_on)
    if category:
        rows = rows.filter(category=category)
    closes = {c.date: c for c in DayClose.objects.filter(user=request.user, date__gte=budget.period.received_on)}
    days = OrderedDict()
    for expense in rows:
        day = days.setdefault(expense.date, {"date": expense.date, "items": [], "total": ZERO, "over": ZERO})
        day["items"].append(expense)
        day["total"] += expense.amount
    for date, day in days.items():
        if date == budget.today:
            day["over"] = budget.over_today if not category else ZERO
        elif date in closes and not category:
            day["over"] = max(closes[date].spent - closes[date].limit, ZERO)
    return render(request, "core/expenses.html", {
        "budget": budget, "form": form, "days": days.values(), "category": category,
        "categories": Expense.CATEGORY_CHOICES,
    })


@login_required
def expense_edit(request, pk):
    expense = get_object_or_404(Expense, pk=pk, user=request.user, shared_expense__isnull=True)
    budget = _budget_or_setup(request)
    form = ExpenseForm(request.POST or None, instance=expense)
    if request.method == "POST" and form.is_valid():
        was_over = bool(budget and budget.over_today > 0)
        form.save()
        messages.success(request, "Expense updated.")
        return _after_expense_saved(request, was_over)
    return render(request, "core/expense_edit.html", {"form": form, "expense": expense, "budget": budget})


@login_required
@require_POST
def expense_delete(request, pk):
    expense = get_object_or_404(Expense, pk=pk, user=request.user, shared_expense__isnull=True)
    expense.delete()
    messages.success(request, "Expense deleted.")
    return redirect("expenses")


# ---------- Budget recovery ----------

@login_required
def recovery(request):
    budget = _budget_or_setup(request)
    if budget is None:
        return redirect("setup")
    if request.method == "POST" and request.POST.get("leftover_rule") in ("roll", "save"):
        profile = request.user.profile
        profile.leftover_rule = request.POST["leftover_rule"]
        profile.save(update_fields=["leftover_rule"])
        messages.success(request, "Leftover rule saved.")
        return redirect("recovery")

    incident = budget.incident
    if incident and budget.needs_plan:
        mood, line = "oops", f"Over by ₱{incident.over:,.0f}. Pick a plan and I'll do the math."
    elif incident and budget.plan == "fast":
        mood, line = "proud", budget.previews["fast"]["note"]
    elif incident:
        mood, line = "happy", f"₱{budget.previews['even']['first']:,.2f} a day. You'll barely feel it."
    else:
        mood, line = "proud", "No overspending to fix. Nice work."
    stretch = incident.days if incident else budget.days_left
    pool = incident.pool if incident else budget.spendable + budget.spent_today
    return render(request, "core/recovery.html", {
        "budget": budget, "incident": incident, "mood": mood, "barya_line": line,
        "stretch": stretch, "pool": pool, "floor_need": budget.floor * stretch,
        "safe": pool >= budget.floor * stretch,
        "selected": budget.plan, "leftover_choices": request.user.profile.LEFTOVER_CHOICES,
    })


# ---------- Allowance ----------

@login_required
def allowance(request):
    budget = _budget_or_setup(request)
    if budget is None:
        return redirect("setup")
    period = budget.period
    schedule_form = ScheduleForm(instance=period, prefix="sched")
    income_form = IncomeForm(prefix="inc")
    if request.method == "POST":
        if request.POST.get("action") == "schedule":
            schedule_form = ScheduleForm(request.POST, instance=period, prefix="sched")
            if schedule_form.is_valid():
                schedule_form.save()
                messages.success(request, "Schedule saved. Your daily limit is recalculated.")
                return redirect("allowance")
        elif request.POST.get("action") == "income":
            income_form = IncomeForm(request.POST, prefix="inc")
            if income_form.is_valid():
                Income.objects.create(user=request.user, date=budget.today, **income_form.cleaned_data)
                messages.success(request, "Added. Your daily limit goes up from here.")
                return redirect("allowance")

    history = [{"title": "Allowance", "date": p.received_on, "amount": p.amount}
               for p in AllowancePeriod.objects.filter(user=request.user)[:8]]
    history += [{"title": f"{i.source}, extra", "date": i.date, "amount": i.amount}
                for i in Income.objects.filter(user=request.user)[:8]]
    history.sort(key=lambda row: row["date"], reverse=True)
    start_spendable = max(budget.spendable + sum(
        (e.amount for e in Expense.objects.filter(user=request.user, date__gte=period.received_on)), ZERO), ZERO)
    return render(request, "core/allowance.html", {
        "budget": budget, "period": period, "schedule_form": schedule_form, "income_form": income_form,
        "history": history[:8], "start_spendable": start_spendable,
    })


# ---------- Bills and payments ----------

@login_required
def bills(request):
    budget = _budget_or_setup(request)
    if budget is None:
        return redirect("setup")
    form = BillForm(request.POST or None, initial={"due_date": budget.today + timedelta(days=7)})
    if request.method == "POST" and form.is_valid():
        bill = form.save(commit=False)
        bill.user = request.user
        bill.save()
        messages.success(request, f"{bill.title} added.")
        return redirect("bills")
    pending = (Repayment.objects.filter(share__shared_expense__paid_by=request.user, status=Repayment.PENDING)
               .select_related("share__member", "share__shared_expense"))
    return render(request, "core/bills.html", {
        "budget": budget, "form": form, "pending": pending,
        "before": [{"bill": bill, "tag": _due_tag(bill, budget.today)} for bill in budget.bills_before],
        "show_form": request.method == "POST",
    })


@login_required
@require_POST
def bill_paid(request, pk):
    bill = get_object_or_404(Bill, pk=pk, user=request.user, paid_on__isnull=True)
    bill.paid_on = timezone.localdate()
    bill.save(update_fields=["paid_on"])
    if bill.is_recurring:
        Bill.objects.create(user=request.user, title=bill.title, amount=bill.amount,
                            due_date=_next_month(bill.due_date), is_recurring=True)
    messages.success(request, f"{bill.title} marked paid.")
    return redirect("bills")


@login_required
@require_POST
def bill_delete(request, pk):
    get_object_or_404(Bill, pk=pk, user=request.user, paid_on__isnull=True).delete()
    messages.success(request, "Bill removed.")
    return redirect("bills")


@login_required
@require_POST
def repayment_answer(request, pk, answer):
    """The receiver confirms a repayment ("Yes, received") or not ("Not yet")."""
    repayment = get_object_or_404(Repayment, pk=pk, share__shared_expense__paid_by=request.user,
                                  status=Repayment.PENDING)
    expense, debtor = repayment.share.shared_expense, repayment.share.member
    if answer == "yes":
        dorm.confirm_repayment(repayment)
        messages.success(request, f"Confirmed. ₱{repayment.amount:,.2f} is now part of your spendable money.")
    else:
        repayment.delete()
        dorm.notify(debtor, "shared", f"{dorm.first_name(request.user)} hasn't received your payment for "
                                      f"{expense.title} yet.")
        messages.info(request, f"Okay. {dorm.first_name(debtor)} will see it is still unpaid.")
    return redirect(request.POST.get("next") or "bills")


# ---------- Spending insights ----------

@login_required
def insights(request):
    budget = _budget_or_setup(request)
    if budget is None:
        return redirect("setup")
    today = budget.today
    mode = request.GET.get("range", "30")
    if mode == "period":
        start, title = budget.period.received_on, "this allowance period"
    elif mode == "month":
        start, title = today.replace(day=1), today.strftime("%B")
    else:
        mode, start, title = "30", today - timedelta(days=29), "the last 30 days"

    rows = Expense.objects.filter(user=request.user, date__gte=start, date__lte=today)
    total = rows.aggregate(t=Sum("amount"))["t"] or ZERO
    span = (today - start).days + 1

    labels = dict(Expense.CATEGORY_CHOICES)
    by_cat = sorted(rows.values("category").annotate(t=Sum("amount")), key=lambda r: r["t"], reverse=True)
    top = by_cat[0]["t"] if by_cat else ZERO
    categories = [{"label": labels[r["category"]], "amount": r["t"],
                   "pct": max(int(r["t"] * 100 / top), 4) if top else 0, "top": i == 0}
                  for i, r in enumerate(by_cat)]

    # Bars: weeks inside the range, or the last six months.
    buckets = []
    if mode == "month":
        cursor = today.replace(day=1)
        for _ in range(6):
            buckets.append((cursor, _next_month(cursor) - timedelta(days=1), cursor.strftime("%b")))
            cursor = (cursor - timedelta(days=1)).replace(day=1)
        buckets.reverse()
    else:
        cursor = start
        while cursor <= today:
            end = min(cursor + timedelta(days=6), today)
            buckets.append((cursor, end, cursor.strftime("%b %-d")))
            cursor = end + timedelta(days=1)
    bars = []
    for first, last, label in buckets:
        amount = (Expense.objects.filter(user=request.user, date__gte=first, date__lte=last)
                  .aggregate(t=Sum("amount"))["t"] or ZERO)
        bars.append({"label": label, "amount": amount, "current": last >= today})
    peak = max((bar["amount"] for bar in bars), default=ZERO)
    for bar in bars:
        bar["pct"] = max(int(bar["amount"] * 100 / peak), 3) if peak else 3

    closes = DayClose.objects.filter(user=request.user, date__gte=start, date__lt=today)
    over_days = sum(1 for c in closes if c.spent > c.limit) + (1 if budget.over_today > 0 else 0)
    headline = ""
    if categories and total:
        share = categories[0]["amount"] / total
        size = "most" if share > Decimal("0.6") else "about half" if share > Decimal("0.4") else "the biggest part"
        headline = f"{categories[0]['label']} was {size} of it."
    return render(request, "core/insights.html", {
        "mode": mode, "title": title, "start": start, "today": today, "total": total,
        "average": money.cents(total / span) if span else ZERO, "over_days": over_days,
        "tracked_days": closes.count() + 1, "categories": categories, "bars": bars,
        "headline": headline, "by_month": mode == "month",
    })


# ---------- My Dorm ----------

@login_required
def my_dorm(request):
    user = request.user
    group = dorm.group_of(user)
    split_form = SplitForm(group=group, prefix="split") if group else None
    join_form, group_form = JoinForm(prefix="join"), GroupForm(prefix="grp")

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create" and not group:
            group_form = GroupForm(request.POST, prefix="grp")
            if group_form.is_valid():
                dorm.create_group(user, group_form.cleaned_data["name"])
                messages.success(request, "Group created. Share the invite code with your roommates.")
                return redirect("dorm")
        elif action == "join":
            join_form = JoinForm(request.POST, prefix="join")
            if join_form.is_valid():
                target = DormGroup.objects.filter(invite_code=join_form.cleaned_data["code"]).first()
                if target is None:
                    join_form.add_error("code", "No group uses that code.")
                elif group and dorm.unsettled_total(user, group) > 0:
                    join_form.add_error("code", "Settle your balances in this group before moving.")
                else:
                    Membership.objects.filter(user=user).delete()
                    Membership.objects.create(group=target, user=user)
                    for member in target.memberships.exclude(user=user):
                        dorm.notify(member.user, "shared", f"{dorm.first_name(user)} joined {target.name}.")
                    messages.success(request, f"You joined {target.name}.")
                    return redirect("dorm")
        elif action == "leave" and group:
            if dorm.unsettled_total(user, group) > 0:
                messages.error(request, "You can't leave while a balance is unsettled.")
            else:
                Membership.objects.filter(user=user).delete()
                messages.success(request, f"You left {group.name}.")
            return redirect("dorm")
        elif action == "split" and group:
            split_form = SplitForm(request.POST, group=group, prefix="split")
            if split_form.is_valid():
                data = split_form.cleaned_data
                dorm.split_bill(group, user, data["title"], data["total"], list(data["members"]))
                messages.success(request, f"{data['title']} added to {group.name}.")
                return redirect("dorm")

    context = {"group": group, "join_form": join_form, "group_form": group_form, "split_form": split_form}
    if group:
        budget = money.build(user)
        members = [m.user for m in group.memberships.select_related("user")]
        shared = []
        for expense in group.expenses.prefetch_related("shares__repayments", "shares__member").select_related("paid_by"):
            shared.append({"expense": expense, "payer": "you" if expense.paid_by_id == user.id
                           else dorm.first_name(expense.paid_by), **dorm.expense_status(expense, user)})
        balances = {}
        for row in (budget.owed_me if budget else money.owed_to_me(user)):
            entry = balances.setdefault(row["by"].id, {"user": row["by"], "amount": ZERO, "pending": None, "mine": False})
            entry["amount"] += row["amount"]
            entry["pending"] = entry["pending"] or row["pending"]
        debts = {}
        for row in (budget.i_owe if budget else money.what_i_owe(user)):
            entry = debts.setdefault(row["to"].id, {"user": row["to"], "amount": ZERO, "shares": []})
            entry["amount"] += row["amount"]
            entry["shares"].append(row["share"])
        context.update({
            "members": [{"user": m, "name": dorm.first_name(m), "me": m.id == user.id,
                         "creator": m.id == group.creator_id} for m in members],
            "shared": shared, "owed_me": balances.values(), "i_owe": debts.values(),
            "can_leave": dorm.unsettled_total(user, group) == 0,
        })
    return render(request, "core/dorm.html", context)


@login_required
@require_POST
def share_repay(request, pk):
    share = get_object_or_404(Share, pk=pk, member=request.user)
    repayment = dorm.record_repayment(share)
    if repayment:
        payer = dorm.first_name(share.shared_expense.paid_by)
        messages.success(request, f"Recorded. {payer} will confirm when the ₱{repayment.amount:,.2f} arrives.")
    return redirect(request.POST.get("next") or "dorm")


@login_required
@require_POST
def shared_delete(request, pk):
    expense = get_object_or_404(SharedExpense, pk=pk, paid_by=request.user)
    if Repayment.objects.filter(share__shared_expense=expense).exists():
        messages.error(request, "Someone already repaid this one, so it can't be deleted.")
    else:
        expense.delete()
        messages.success(request, "Shared expense deleted.")
    return redirect("dorm")


@login_required
@require_POST
def remind(request, user_id):
    group = dorm.group_of(request.user)
    member = get_object_or_404(Membership, group=group, user_id=user_id).user if group else None
    if member:
        owed = sum((row["amount"] for row in money.owed_to_me(request.user) if row["by"].id == member.id), ZERO)
        if owed > 0:
            dorm.notify(member, "shared", f"{dorm.first_name(request.user)} sent a reminder: you owe ₱{owed:,.2f}.")
            messages.success(request, f"Reminder sent to {dorm.first_name(member)}.")
    return redirect(request.POST.get("next") or "dorm")


# ---------- Profile and settings ----------

@login_required
def settings_page(request):
    user, profile = request.user, request.user.profile
    profile_form = ProfileForm(user=user, prefix="prof",
                               initial={"full_name": user.get_full_name(), "email": user.email})
    budgeting_form = BudgetingForm(instance=profile, prefix="bud")
    alerts_form = AlertsForm(instance=profile, prefix="al")
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "profile":
            profile_form = ProfileForm(request.POST, user=user, prefix="prof")
            if profile_form.is_valid():
                profile_form.save()
                messages.success(request, "Profile saved.")
                return redirect("settings")
        elif action == "budgeting":
            budgeting_form = BudgetingForm(request.POST, instance=profile, prefix="bud")
            if budgeting_form.is_valid():
                budgeting_form.save()
                messages.success(request, "Budgeting preferences saved.")
                return redirect("settings")
        elif action == "alerts":
            alerts_form = AlertsForm(request.POST, instance=profile, prefix="al")
            if alerts_form.is_valid():
                alerts_form.save()
                messages.success(request, "Reminders saved.")
                return redirect("settings")
        elif action == "delete":
            group = dorm.group_of(user)
            if request.POST.get("confirm") != "DELETE":
                messages.error(request, "Type DELETE to confirm.")
            elif group and dorm.unsettled_total(user, group) > 0:
                messages.error(request, "Settle your dorm balances before deleting your account.")
            elif user.groups_created.exists() and group and group.memberships.count() > 1:
                messages.error(request, "You created a dorm group that still has members.")
            else:
                user.groups_created.all().delete()
                auth_logout(request)
                user.delete()
                return redirect("landing")
            return redirect("settings")
    return render(request, "core/settings.html", {
        "profile_form": profile_form, "budgeting_form": budgeting_form, "alerts_form": alerts_form,
        "group": dorm.group_of(user),
    })

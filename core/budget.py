"""DormWise money rules. Decide once, use everywhere.

spendable   = money on hand - bills due before next allowance
              - what you owe roommates - savings
daily limit = spendable at the start of the day / days left
              (today counts, the next allowance day does not)

Today's limit is fixed at midnight, Philippine time: overspending today only
changes the plan from tomorrow. Everything here is Decimal, never float.
"""
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from django.db.models import Sum
from django.utils import timezone

from .models import AllowancePeriod, Bill, DayClose, Expense, Income, Repayment, Share

ZERO = Decimal("0")
CENT = Decimal("0.01")
FAST_DAYS = 3  # "Fix it by Friday" recovers over three days.


# ---------- Pure math (unit tested) ----------

def cents(value, rounding=ROUND_HALF_UP):
    return Decimal(value).quantize(CENT, rounding=rounding)


def daily_limit(pool, days_left):
    """Spendable money divided by the days left. Never negative."""
    return cents(max(Decimal(pool), ZERO) / max(int(days_left), 1))


def fast_limit(pool, days_left, normal, tight_days=FAST_DAYS):
    """Limit for the tight days of 'Fix it by Friday'.

    Returns None when there are not enough days left to go back to normal.
    """
    if days_left <= tight_days or tight_days < 1:
        return None
    return cents((Decimal(pool) - Decimal(normal) * (days_left - tight_days)) / tight_days)


def split_equally(total, people):
    """Equal split in centavos. Returns (share, leftover the payer absorbs)."""
    total_cents = int((Decimal(total) * 100).to_integral_value(rounding=ROUND_HALF_UP))
    share_cents = total_cents // people
    leftover = total_cents - share_cents * people
    return Decimal(share_cents) / 100, Decimal(leftover) / 100


def plan_amounts(plan, pool, days, normal, floor, tight_days=FAST_DAYS):
    """Daily amounts for the next `days` days under a plan.

    Returns (amounts, ok). `ok` is False when 'fast' cannot be offered (too
    few days, or it would dip under the essentials floor) and the even
    spread is returned instead.
    """
    if days < 1:
        return [], False
    even = daily_limit(pool, days)
    if plan == "fast":
        tight = fast_limit(pool, days, normal, tight_days)
        if tight is not None and floor <= tight <= even:
            return [tight] * tight_days + [cents(normal)] * (days - tight_days), True
        return [even] * days, False
    return [even] * days, True


# ---------- Reading a student's money ----------

def _total(queryset, name="amount"):
    return queryset.aggregate(total=Sum(name))["total"] or ZERO


def current_period(user):
    return AllowancePeriod.objects.filter(user=user).first()


def what_i_owe(user):
    """Unpaid shares of bills a roommate paid. A recorded repayment counts
    as paid from the debtor's side, even before the receiver confirms."""
    rows = []
    shares = (
        Share.objects.filter(member=user)
        .exclude(shared_expense__paid_by=user)
        .select_related("shared_expense", "shared_expense__paid_by")
    )
    for share in shares:
        outstanding = share.amount - _total(share.repayments.all())
        if outstanding > ZERO:
            rows.append({"share": share, "to": share.shared_expense.paid_by, "amount": outstanding})
    return rows


def owed_to_me(user):
    """Roommates' shares of bills I paid, until I confirm their repayment."""
    rows = []
    shares = (
        Share.objects.filter(shared_expense__paid_by=user)
        .exclude(member=user)
        .select_related("shared_expense", "member")
    )
    for share in shares:
        confirmed = _total(share.repayments.filter(status=Repayment.CONFIRMED))
        outstanding = share.amount - confirmed
        if outstanding > ZERO:
            pending = share.repayments.filter(status=Repayment.PENDING).first()
            rows.append({"share": share, "by": share.member, "amount": outstanding, "pending": pending})
    return rows


@dataclass
class Incident:
    """A day that went over its limit, and the stretch that absorbs it."""

    day: date
    limit: Decimal
    spent: Decimal
    over: Decimal
    start: date      # first day of the recovery stretch
    pool: Decimal    # spendable money at the start of that stretch
    days: int        # days in the stretch
    is_today: bool


@dataclass
class Budget:
    period: AllowancePeriod
    today: date
    days_left: int
    on_hand: Decimal
    bills_reserved: Decimal
    owed_total: Decimal
    set_aside: Decimal          # bills + what you owe roommates
    savings: Decimal
    spendable: Decimal
    spent_today: Decimal
    limit_today: Decimal
    left_today: Decimal
    floor: Decimal
    shortfall: bool
    allowance_due: bool
    plan: str
    incident: Incident = None
    needs_plan: bool = False
    previews: dict = field(default_factory=dict)
    i_owe: list = field(default_factory=list)
    owed_me: list = field(default_factory=list)
    bills_before: list = field(default_factory=list)
    bills_after: list = field(default_factory=list)

    @property
    def over_today(self):
        return max(self.spent_today - self.limit_today, ZERO)

    @property
    def active_preview(self):
        return self.previews.get(self.plan if self.plan in self.previews else "even")


def _limit_on(period, day, pool, days_left, floor):
    """The limit a given day gets, honouring a 'fast' plan in progress."""
    even = daily_limit(pool, days_left)
    if period.recovery_plan == "fast" and period.plan_started_on and period.plan_normal_daily:
        gone = (day - period.plan_started_on).days
        tight = FAST_DAYS - gone
        if gone >= 0 and tight > 0:
            fast = fast_limit(pool, days_left, period.plan_normal_daily, tight)
            if fast is not None and floor <= fast <= even:
                return fast
    return even


def _preview(plan, incident, pool, days, start, normal, floor, today, tight_days=FAST_DAYS):
    amounts, ok = plan_amounts(plan, pool, days, normal, floor, tight_days)
    rows = []
    for index, amount in enumerate(amounts):
        day = start + timedelta(days=index)
        rows.append({
            "date": day,
            "label": "Today" if day == today else day.strftime("%a"),
            "amount": amount,
            "tight": plan == "fast" and ok and index < tight_days,
            "is_today": day == today,
        })
    first = amounts[0] if amounts else ZERO
    if plan == "fast" and ok:
        back = start + timedelta(days=tight_days)
        count = {1: "One tighter day", 2: "Two tighter days", 3: "Three tighter days"}[tight_days]
        note = f"{count}, then ₱{cents(normal):,.2f} a day again from {back.strftime('%A')}."
        figure = f"₱{first:,.2f}, then ₱{cents(normal):,.2f}"
    else:
        diff = cents(normal) - first
        if incident and diff > ZERO:
            note = f"About ₱{diff:,.0f} less each day, for all {days} days."
        else:
            note = f"₱{first:,.2f} a day for the next {days} days."
        figure = f"₱{first:,.2f} a day"
    return {"plan": plan, "days": rows, "ok": ok, "first": first, "note": note, "figure": figure}


def build(user, today=None, _synced=False):
    """Everything the pages need to know about a student's money today."""
    today = today or timezone.localdate()
    period = current_period(user)
    if period is None:
        return None
    floor = user.profile.essentials_floor
    start = period.received_on

    expenses = Expense.objects.filter(user=user, date__gte=start)
    lent = Share.objects.filter(shared_expense__paid_by=user, shared_expense__date__gte=start).exclude(member=user)
    paid_back_out = Repayment.objects.filter(share__member=user, recorded_on__gte=start)
    paid_back_in = Repayment.objects.filter(
        share__shared_expense__paid_by=user, status=Repayment.CONFIRMED, confirmed_on__gte=start
    )
    on_hand = (
        period.amount
        + _total(Income.objects.filter(user=user, date__gte=start))
        + _total(paid_back_in)
        - _total(expenses)
        - _total(Bill.objects.filter(user=user, paid_on__gte=start))
        - _total(lent)
        - _total(paid_back_out)
    )

    unpaid = Bill.objects.filter(user=user, paid_on__isnull=True)
    bills_before = list(unpaid.filter(due_date__lt=period.next_allowance_on))
    bills_after = list(unpaid.filter(due_date__gte=period.next_allowance_on))
    bills_reserved = sum((bill.amount for bill in bills_before), ZERO)
    i_owe = what_i_owe(user)
    owed_total = sum((row["amount"] for row in i_owe), ZERO)

    spendable = on_hand - bills_reserved - owed_total - period.savings_amount
    days_left = max((period.next_allowance_on - today).days, 1)
    spent_today = _total(expenses.filter(date=today))
    pool_today = spendable + spent_today
    limit_today = _limit_on(period, today, pool_today, days_left, floor)

    # Yesterday, rebuilt from what is known now.
    yesterday = today - timedelta(days=1)
    incident = None
    y_limit = y_spent = None
    if yesterday >= start:
        y_spent = _total(expenses.filter(date=yesterday))
        y_limit = _limit_on(period, yesterday, pool_today + y_spent, days_left + 1, floor)

    # Midnight rollover: close yesterday once, and bank the leftover if asked.
    if not _synced and y_limit is not None and period.rolled_over_on != today:
        DayClose.objects.update_or_create(
            user=user, date=yesterday, defaults={"limit": y_limit, "spent": y_spent}
        )
        if user.profile.leftover_rule == "save" and y_spent < y_limit:
            period.savings_amount += y_limit - y_spent
        period.rolled_over_on = today
        period.save(update_fields=["savings_amount", "rolled_over_on"])
        return build(user, today, _synced=True)

    if spent_today > limit_today and days_left > 1:
        incident = Incident(today, limit_today, spent_today, spent_today - limit_today,
                            today + timedelta(days=1), spendable, days_left - 1, True)
    elif y_limit is not None and y_spent > y_limit:
        incident = Incident(yesterday, y_limit, y_spent, y_spent - y_limit,
                            today, pool_today, days_left, False)

    needs_plan = bool(incident) and (period.plan_started_on is None or period.plan_started_on < incident.start)
    default = user.profile.default_recovery_plan
    if needs_plan and default in ("even", "fast") and not _synced:
        choose_plan(period, incident, default, floor)
        return build(user, today, _synced=True)

    tight_days = FAST_DAYS
    if incident:
        p_start, p_pool, p_days, normal = incident.start, incident.pool, incident.days, incident.limit
    else:
        if period.recovery_plan == "fast" and period.plan_started_on:
            # A tight stretch already under way: show what is left of it.
            tight_days = min(FAST_DAYS - (today - period.plan_started_on).days, FAST_DAYS)
        p_start, p_pool, p_days = today, pool_today, days_left
        normal = period.plan_normal_daily if period.recovery_plan == "fast" and period.plan_normal_daily else limit_today
    previews = {
        name: _preview(name, incident, p_pool, p_days, p_start, normal, floor, today, tight_days)
        for name in ("even", "fast")
    }
    plan = period.recovery_plan if period.recovery_plan in ("even", "fast") else "even"
    if plan == "fast" and not previews["fast"]["ok"] and not incident:
        # The tight stretch is over (or no longer fits): show the even spread.
        plan = "even"

    return Budget(
        period=period, today=today, days_left=days_left, on_hand=on_hand,
        bills_reserved=bills_reserved, owed_total=owed_total,
        set_aside=bills_reserved + owed_total, savings=period.savings_amount,
        spendable=spendable, spent_today=spent_today, limit_today=limit_today,
        left_today=limit_today - spent_today, floor=floor,
        shortfall=daily_limit(pool_today, days_left) < floor,
        allowance_due=today >= period.next_allowance_on,
        plan=plan, incident=incident, needs_plan=needs_plan, previews=previews,
        i_owe=i_owe, owed_me=owed_to_me(user), bills_before=bills_before, bills_after=bills_after,
    )


def choose_plan(period, incident, plan, floor):
    """Lock in a recovery plan for the stretch that follows an overspend."""
    if plan == "fast":
        _, ok = plan_amounts("fast", incident.pool, incident.days, incident.limit, floor)
        if not ok:
            plan = "even"
    period.recovery_plan = plan
    period.plan_started_on = incident.start
    period.plan_normal_daily = incident.limit
    period.save(update_fields=["recovery_plan", "plan_started_on", "plan_normal_daily"])
    return plan


def tomorrow_limit_after(budget, extra):
    """Tomorrow's limit if `extra` more pesos are spent today."""
    if budget.days_left <= 1:
        return None
    return daily_limit(budget.spendable - Decimal(extra), budget.days_left - 1)

from decimal import Decimal

from django.conf import settings
from django.db import models

User = settings.AUTH_USER_MODEL

MONEY = {"max_digits": 10, "decimal_places": 2}


class Profile(models.Model):
    PLAN_CHOICES = [
        ("even", "Little by little"),
        ("fast", "Fix it by Friday"),
        ("ask", "Ask me each time"),
    ]
    LEFTOVER_CHOICES = [("roll", "Roll it forward"), ("save", "Move to savings")]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    essentials_floor = models.DecimalField(default=Decimal("120.00"), **MONEY)
    default_recovery_plan = models.CharField(max_length=4, choices=PLAN_CHOICES, default="ask")
    leftover_rule = models.CharField(max_length=4, choices=LEFTOVER_CHOICES, default="roll")
    alert_over_limit = models.BooleanField(default=True)
    alert_runway = models.BooleanField(default=True)
    alert_bill_due = models.BooleanField(default=True)
    alert_shared = models.BooleanField(default=True)
    email_alerts = models.BooleanField(default=False)

    def __str__(self):
        return f"Profile of {self.user}"

    @property
    def initials(self):
        name = (self.user.get_full_name() or self.user.get_username()).strip()
        parts = name.replace("@", " ").split()
        letters = [p[0] for p in parts[:2]] or ["?"]
        return "".join(letters).upper()


class AllowancePeriod(models.Model):
    FREQUENCY_CHOICES = [
        ("weekly", "Weekly"),
        ("biweekly", "Every 2 weeks"),
        ("monthly", "Monthly"),
        ("custom", "Custom"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="periods")
    amount = models.DecimalField(**MONEY)
    received_on = models.DateField()
    next_allowance_on = models.DateField()
    frequency = models.CharField(max_length=8, choices=FREQUENCY_CHOICES, default="custom")
    savings_amount = models.DecimalField(default=Decimal("0.00"), **MONEY)

    # Smart Budget Recovery state. "even" needs no extra state; "fast" needs
    # the day the tight stretch starts and the normal limit to return to.
    recovery_plan = models.CharField(max_length=4, blank=True, default="")
    plan_started_on = models.DateField(null=True, blank=True)
    plan_normal_daily = models.DecimalField(null=True, blank=True, **MONEY)

    # Last day the midnight rollover ran for this period.
    rolled_over_on = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-received_on", "-id"]

    def __str__(self):
        return f"{self.user}: {self.amount} on {self.received_on}"

    @property
    def total_days(self):
        return max((self.next_allowance_on - self.received_on).days, 1)


class Income(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="incomes")
    amount = models.DecimalField(**MONEY)
    source = models.CharField(max_length=120)
    date = models.DateField()

    class Meta:
        ordering = ["-date", "-id"]


class Expense(models.Model):
    CATEGORY_CHOICES = [
        ("food", "Food"),
        ("transport", "Transport"),
        ("laundry", "Laundry"),
        ("school", "School supplies"),
        ("personal", "Personal"),
        ("other", "Other"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="expenses")
    amount = models.DecimalField(**MONEY)
    category = models.CharField(max_length=10, choices=CATEGORY_CHOICES, default="food")
    note = models.CharField(max_length=120, blank=True)
    date = models.DateField()
    # Set when this row is the payer's own share of a dorm bill.
    shared_expense = models.ForeignKey(
        "SharedExpense", null=True, blank=True, on_delete=models.CASCADE, related_name="payer_expenses"
    )

    class Meta:
        ordering = ["-date", "-id"]

    @property
    def title(self):
        return self.note or self.get_category_display()


class Bill(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="bills")
    title = models.CharField(max_length=120)
    amount = models.DecimalField(**MONEY)
    due_date = models.DateField()
    is_recurring = models.BooleanField(default=False)
    paid_on = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["due_date", "id"]


class DormGroup(models.Model):
    name = models.CharField(max_length=60)
    invite_code = models.CharField(max_length=8, unique=True)
    creator = models.ForeignKey(User, on_delete=models.PROTECT, related_name="groups_created")

    def __str__(self):
        return self.name


class Membership(models.Model):
    group = models.ForeignKey(DormGroup, on_delete=models.CASCADE, related_name="memberships")
    # One dorm group at a time per student.
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="membership")
    joined_on = models.DateField(auto_now_add=True)

    class Meta:
        ordering = ["joined_on", "id"]


class SharedExpense(models.Model):
    group = models.ForeignKey(DormGroup, on_delete=models.CASCADE, related_name="expenses")
    title = models.CharField(max_length=120)
    total = models.DecimalField(**MONEY)
    paid_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="shared_paid")
    date = models.DateField()

    class Meta:
        ordering = ["-date", "-id"]


class Share(models.Model):
    shared_expense = models.ForeignKey(SharedExpense, on_delete=models.CASCADE, related_name="shares")
    member = models.ForeignKey(User, on_delete=models.CASCADE, related_name="shares")
    amount = models.DecimalField(**MONEY)

    class Meta:
        unique_together = [("shared_expense", "member")]


class Repayment(models.Model):
    PENDING, CONFIRMED = "pending", "confirmed"

    share = models.ForeignKey(Share, on_delete=models.CASCADE, related_name="repayments")
    amount = models.DecimalField(**MONEY)
    recorded_on = models.DateField()
    status = models.CharField(max_length=9, default=PENDING)
    confirmed_on = models.DateField(null=True, blank=True)


class DayClose(models.Model):
    """What a finished day looked like: the limit it had and what was spent."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="day_closes")
    date = models.DateField()
    limit = models.DecimalField(**MONEY)
    spent = models.DecimalField(**MONEY)

    class Meta:
        unique_together = [("user", "date")]
        ordering = ["-date"]


class Alert(models.Model):
    KINDS = [
        ("over", "Over today's limit"),
        ("runway", "Money may not last"),
        ("bill", "Bill due soon"),
        ("shared", "Dorm group"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="alerts")
    kind = models.CharField(max_length=8, choices=KINDS)
    message = models.CharField(max_length=240)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

from datetime import date, timedelta
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.test import TestCase

from . import budget, dorm
from .models import AllowancePeriod, Bill, Expense, Profile, Repayment

User = get_user_model()


class MathTests(TestCase):
    def test_brief_example_daily_limit(self):
        spendable = D("3500") - D("1200") - D("300")
        self.assertEqual(budget.daily_limit(spendable, 10), D("200.00"))

    def test_little_by_little(self):
        self.assertEqual(budget.daily_limit(D("1700"), 9), D("188.89"))

    def test_fix_it_by_friday(self):
        self.assertEqual(budget.fast_limit(D("1700"), 9, D("200")), D("166.67"))
        amounts, ok = budget.plan_amounts("fast", D("1700"), 9, D("200"), D("120"))
        self.assertTrue(ok)
        self.assertEqual(amounts, [D("166.67")] * 3 + [D("200.00")] * 6)

    def test_fast_plan_never_goes_under_the_floor(self):
        amounts, ok = budget.plan_amounts("fast", D("1500"), 9, D("200"), D("120"))
        self.assertFalse(ok)  # (1500 - 1200) / 3 = 100, under the 120 floor
        self.assertEqual(amounts, [D("166.67")] * 9)

    def test_split_leftover_centavo_goes_to_payer(self):
        self.assertEqual(budget.split_equally(D("1000"), 3), (D("333.33"), D("0.01")))
        self.assertEqual(budget.split_equally(D("900"), 3), (D("300"), D("0")))

    def test_never_a_zero_day_state(self):
        self.assertEqual(budget.daily_limit(D("500"), 0), D("500.00"))
        self.assertEqual(budget.daily_limit(D("-50"), 3), D("0.00"))


class BudgetFlowTests(TestCase):
    def setUp(self):
        self.today = date(2026, 10, 14)
        self.user = self._user("andrea")
        self.period = AllowancePeriod.objects.create(
            user=self.user, amount=D("3500"), received_on=self.today - timedelta(days=1),
            next_allowance_on=self.today + timedelta(days=9), savings_amount=D("300"),
            rolled_over_on=self.today,
        )
        Bill.objects.create(user=self.user, title="Bills", amount=D("1200"), due_date=self.today + timedelta(days=4))
        Expense.objects.create(user=self.user, amount=D("300"), date=self.today - timedelta(days=1))

    def _user(self, name):
        user = User.objects.create_user(username=f"{name}@school.edu.ph", first_name=name.title())
        Profile.objects.create(user=user)
        return user

    def test_overspend_changes_tomorrow_not_today(self):
        b = budget.build(self.user, self.today)
        self.assertEqual(b.spendable, D("1700"))
        self.assertEqual(b.limit_today, D("188.89"))
        self.assertEqual(b.incident.over, D("100.00"))
        self.assertTrue(b.needs_plan)
        Expense.objects.create(user=self.user, amount=D("45"), date=self.today)
        b = budget.build(self.user, self.today)
        self.assertEqual(b.limit_today, D("188.89"))  # fixed until midnight
        self.assertEqual(b.left_today, D("143.89"))

    def test_choosing_fix_it_by_friday(self):
        b = budget.build(self.user, self.today)
        budget.choose_plan(b.period, b.incident, "fast", b.floor)
        b = budget.build(self.user, self.today)
        self.assertEqual(b.limit_today, D("166.67"))
        self.assertFalse(b.needs_plan)
        for offset in range(3):  # stick to the three tight days
            Expense.objects.create(user=self.user, amount=D("166.67"), date=self.today + timedelta(days=offset))
        later = budget.build(self.user, self.today + timedelta(days=3))
        self.assertEqual(later.limit_today, D("200.00"))  # back to normal on day four

    def test_bills_after_next_allowance_are_not_reserved(self):
        Bill.objects.create(user=self.user, title="Rent", amount=D("2500"), due_date=self.today + timedelta(days=18))
        self.assertEqual(budget.build(self.user, self.today).spendable, D("1700"))

    def test_money_owed_to_me_waits_for_confirmation(self):
        kat = self._user("kat")
        group = dorm.create_group(self.user, "Room 3B")
        group.memberships.create(user=kat)
        expense = dorm.split_bill(group, self.user, "Supplies", D("100"), [self.user, kat], self.today)
        b = budget.build(self.user, self.today)
        self.assertEqual(b.spendable, D("1600"))  # paid 100: own 50 is spending, 50 is lent out
        share = expense.shares.get(member=kat)
        self.assertEqual(budget.build(kat, self.today), None)  # kat has no allowance yet
        repayment = dorm.record_repayment(share, self.today)
        self.assertEqual(budget.build(self.user, self.today).spendable, D("1600"))
        dorm.confirm_repayment(repayment, self.today)
        self.assertEqual(repayment.status, Repayment.CONFIRMED)
        self.assertEqual(budget.build(self.user, self.today).spendable, D("1650"))

from datetime import timedelta
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from core import dorm
from core.models import (AllowancePeriod, Bill, DayClose, DormGroup, Expense, Income, Membership, Profile,
                         Repayment, SharedExpense)

User = get_user_model()
PASSWORD = "dormwise2026"


class Command(BaseCommand):
    help = "Create the Room 3B demo: Andrea, Paolo and Kat, one day after an overspend."

    @transaction.atomic
    def handle(self, *args, **options):
        today = timezone.localdate()
        day = lambda offset: today + timedelta(days=offset)  # noqa: E731

        emails = [f"{name}@school.edu.ph" for name in ("andrea.reyes", "paolo.santos", "kat.lim")]
        DormGroup.objects.filter(creator__username__in=emails).delete()
        User.objects.filter(username__in=emails).delete()

        def student(email, first, last):
            user = User.objects.create_user(username=email, email=email, password=PASSWORD,
                                            first_name=first, last_name=last)
            Profile.objects.create(user=user)
            return user

        andrea = student(emails[0], "Andrea", "Reyes")
        paolo = student(emails[1], "Paolo", "Santos")
        kat = student(emails[2], "Kat", "Lim")

        # Andrea: ₱3,500 yesterday, next allowance in 9 days.
        AllowancePeriod.objects.create(user=andrea, amount=D("3500"), received_on=day(-1), next_allowance_on=day(9),
                                       frequency="custom", savings_amount=D("300"), rolled_over_on=day(-1))
        for offset in (-11, -21):
            AllowancePeriod.objects.create(user=andrea, amount=D("3500"), received_on=day(offset),
                                           next_allowance_on=day(offset + 10), rolled_over_on=day(offset + 10))
        Income.objects.create(user=andrea, amount=D("400"), source="Tutoring", date=day(-6))
        for user in (paolo, kat):
            AllowancePeriod.objects.create(user=user, amount=D("4000"), received_on=day(-3),
                                           next_allowance_on=day(11), rolled_over_on=today)

        Bill.objects.create(user=andrea, title="Wi-Fi share", amount=D("450"), due_date=day(4), is_recurring=True)
        Bill.objects.create(user=andrea, title="Water share", amount=D("150"), due_date=day(7), is_recurring=True)
        Bill.objects.create(user=andrea, title="Electricity share", amount=D("300"), due_date=day(8), is_recurring=True)
        Bill.objects.create(user=andrea, title="Dorm rent", amount=D("2500"), due_date=day(18), is_recurring=True)

        # Yesterday went ₱100 over a ₱200 limit. Today has one small expense.
        for amount, category, note in ((120, "laundry", "Laundry"), (85, "food", "Lunch with blockmates"),
                                       (69, "personal", "Mobile load"), (26, "transport", "Jeepney fare")):
            Expense.objects.create(user=andrea, amount=D(amount), category=category, note=note, date=day(-1))
        Expense.objects.create(user=andrea, amount=D("45"), category="food", note="Siomai rice", date=today)

        # Earlier spending, so Insights has something to chart.
        pattern = [(95, "food", "Carinderia lunch"), (26, "transport", "Jeepney fare"), (60, "food", "Dinner"),
                   (120, "laundry", "Laundry"), (85, "school", "Photocopies"), (50, "personal", "Mobile load"),
                   (40, "other", "Org contribution")]
        for offset in range(-29, -1):
            spent = D("0")
            for index in range(2 + offset % 2):
                amount, category, note = pattern[(index - offset) % len(pattern)]
                Expense.objects.create(user=andrea, amount=D(amount), category=category, note=note, date=day(offset))
                spent += D(amount)
            DayClose.objects.create(user=andrea, date=day(offset), limit=D("320") if offset % 8 else D("150"), spent=spent)

        group = DormGroup.objects.create(name="Room 3B", invite_code="R3B-7KQ2", creator=andrea)
        for user in (andrea, paolo, kat):
            Membership.objects.create(group=group, user=user)

        def shared(title, total, payer, offset):
            expense = SharedExpense.objects.create(group=group, title=title, total=D(total), paid_by=payer, date=day(offset))
            for user in (andrea, paolo, kat):
                expense.shares.create(member=user, amount=D(total) / 3)
            return expense

        def repay(expense, member, offset, confirmed=True):
            share = expense.shares.get(member=member)
            Repayment.objects.create(share=share, amount=share.amount, recorded_on=day(offset),
                                     status=Repayment.CONFIRMED if confirmed else Repayment.PENDING,
                                     confirmed_on=day(offset) if confirmed else None)

        wifi = shared("Wi-Fi, last month", 1350, kat, -20)
        repay(wifi, andrea, -19)
        repay(wifi, paolo, -18)
        electricity = shared("Electricity, last month", 900, andrea, -12)
        repay(electricity, paolo, -10)
        repay(electricity, kat, 0, confirmed=False)   # waiting for Andrea to confirm
        groceries = shared("Groceries", 900, paolo, -4)
        repay(groceries, kat, -3)                      # Andrea still owes Paolo ₱300
        dorm.notify(andrea, "shared", "Kat says they sent ₱300.00 for Electricity, last month. Confirm it in Bills.")

        self.stdout.write(self.style.SUCCESS(
            f"Demo ready. Log in as {emails[0]} (or paolo.santos@, kat.lim@) with password {PASSWORD}"))

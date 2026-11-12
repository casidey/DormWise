from django.contrib import admin

from . import models

for model in (models.Profile, models.AllowancePeriod, models.Income, models.Expense, models.Bill,
              models.DormGroup, models.Membership, models.SharedExpense, models.Share,
              models.Repayment, models.DayClose, models.Alert):
    admin.site.register(model)

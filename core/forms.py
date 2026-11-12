from decimal import Decimal

from django import forms
from django.contrib.auth import authenticate, get_user_model, password_validation
from django.utils import timezone

from .models import AllowancePeriod, Bill, Expense, Profile

User = get_user_model()

MONEY_ATTRS = {"class": "input input-mono", "inputmode": "decimal", "step": "0.01", "min": "0"}


def money_field(label, **kwargs):
    kwargs.setdefault("min_value", Decimal("0"))
    return forms.DecimalField(label=label, max_digits=10, decimal_places=2,
                              widget=forms.NumberInput(attrs=dict(MONEY_ATTRS)), **kwargs)


class StyledMixin:
    """Gives plain widgets the DormWise input class."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, (forms.RadioSelect, forms.CheckboxInput, forms.CheckboxSelectMultiple)):
                continue
            widget.attrs.setdefault("class", "input")


class SignupForm(StyledMixin, forms.Form):
    full_name = forms.CharField(label="Full name", max_length=120)
    email = forms.EmailField(label="Email")
    password = forms.CharField(label="Password", widget=forms.PasswordInput, help_text="At least 8 characters.")

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(username=email).exists():
            raise forms.ValidationError("That email already has an account. Try logging in.")
        return email

    def clean_password(self):
        password = self.cleaned_data["password"]
        password_validation.validate_password(password)
        return password

    def save(self):
        first, _, last = self.cleaned_data["full_name"].strip().partition(" ")
        user = User.objects.create_user(
            username=self.cleaned_data["email"], email=self.cleaned_data["email"],
            password=self.cleaned_data["password"], first_name=first, last_name=last,
        )
        Profile.objects.create(user=user)
        return user


class LoginForm(StyledMixin, forms.Form):
    email = forms.EmailField(label="Email")
    password = forms.CharField(label="Password", widget=forms.PasswordInput)

    def clean(self):
        data = super().clean()
        if data.get("email") and data.get("password"):
            self.user = authenticate(username=data["email"].lower(), password=data["password"])
            if self.user is None:
                raise forms.ValidationError("That email and password don't match.")
        return data


class SetupForm(StyledMixin, forms.Form):
    amount = money_field("Amount you received (₱)", min_value=Decimal("1"))
    days = forms.IntegerField(label="Days until next allowance", min_value=1, max_value=120,
                              widget=forms.NumberInput(attrs={"class": "input input-mono", "min": "1"}))
    frequency = forms.ChoiceField(label="How often does it arrive?", choices=AllowancePeriod.FREQUENCY_CHOICES,
                                  widget=forms.RadioSelect, initial="custom")
    savings = money_field("Put into savings (₱)", required=False)
    floor = money_field("Essentials floor per day (₱)",
                        help_text="Meals and fare. Recovery never plans below this.")


class ExpenseForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = Expense
        fields = ["amount", "category", "note", "date"]
        labels = {"amount": "Amount (₱)"}
        widgets = {
            "amount": forms.NumberInput(attrs={**MONEY_ATTRS, "class": "input input-mono input-xl", "min": "0.01"}),
            "category": forms.RadioSelect,
            "date": forms.DateInput(attrs={"type": "date"}),
        }

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if amount <= 0:
            raise forms.ValidationError("Enter an amount above zero.")
        return amount

    def clean_date(self):
        date = self.cleaned_data["date"]
        if date > timezone.localdate():
            raise forms.ValidationError("That date hasn't happened yet.")
        return date


class BillForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = Bill
        fields = ["title", "amount", "due_date", "is_recurring"]
        labels = {"title": "What is it?", "amount": "Amount (₱)", "due_date": "Due date",
                  "is_recurring": "Repeats every month"}
        widgets = {
            "amount": forms.NumberInput(attrs=dict(MONEY_ATTRS)),
            "due_date": forms.DateInput(attrs={"type": "date"}),
        }


class IncomeForm(StyledMixin, forms.Form):
    amount = money_field("Amount (₱)", min_value=Decimal("0.01"))
    source = forms.CharField(label="From", max_length=120)


class ScheduleForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = AllowancePeriod
        fields = ["amount", "frequency", "next_allowance_on"]
        labels = {"amount": "Usual amount (₱)", "frequency": "Arrives", "next_allowance_on": "Next allowance date"}
        help_texts = {"next_allowance_on": "Late allowance? Move this date and your daily limit recalculates."}
        widgets = {
            "amount": forms.NumberInput(attrs=dict(MONEY_ATTRS)),
            "frequency": forms.RadioSelect,
            "next_allowance_on": forms.DateInput(attrs={"type": "date"}),
        }

    def clean_next_allowance_on(self):
        date = self.cleaned_data["next_allowance_on"]
        if date <= timezone.localdate():
            raise forms.ValidationError("Pick a date after today.")
        return date


class ProfileForm(StyledMixin, forms.Form):
    full_name = forms.CharField(label="Full name", max_length=120)
    email = forms.EmailField(label="Email")

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(username=email).exclude(pk=self.user.pk).exists():
            raise forms.ValidationError("Another account already uses that email.")
        return email

    def save(self):
        first, _, last = self.cleaned_data["full_name"].strip().partition(" ")
        self.user.first_name, self.user.last_name = first, last
        self.user.email = self.user.username = self.cleaned_data["email"]
        self.user.save()


class BudgetingForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = Profile
        fields = ["essentials_floor", "default_recovery_plan", "leftover_rule"]
        labels = {"essentials_floor": "Essentials floor per day (₱)",
                  "default_recovery_plan": "Default recovery plan",
                  "leftover_rule": "Leftover money at day's end"}
        widgets = {
            "essentials_floor": forms.NumberInput(attrs=dict(MONEY_ATTRS)),
            "default_recovery_plan": forms.RadioSelect,
            "leftover_rule": forms.RadioSelect,
        }


class AlertsForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ["alert_over_limit", "alert_runway", "alert_bill_due", "alert_shared", "email_alerts"]
        labels = {
            "alert_over_limit": "When I go over today's limit",
            "alert_runway": "When my money may not last until next allowance",
            "alert_bill_due": "Two days before a bill is due",
            "alert_shared": "When a roommate adds a shared expense",
            "email_alerts": "Also send these to my email",
        }


class SplitForm(StyledMixin, forms.Form):
    title = forms.CharField(label="What was it for?", max_length=120)
    total = money_field("Total (₱), paid by you", min_value=Decimal("0.01"))
    members = forms.ModelMultipleChoiceField(label="Split equally between", queryset=User.objects.none(),
                                             widget=forms.CheckboxSelectMultiple,
                                             error_messages={"required": "Pick at least one person."})

    def __init__(self, *args, group, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["members"].queryset = User.objects.filter(membership__group=group).order_by("membership__id")


class JoinForm(StyledMixin, forms.Form):
    code = forms.CharField(label="Invite code", max_length=8,
                           widget=forms.TextInput(attrs={"class": "input input-mono", "placeholder": "ABC-1234",
                                                         "autocapitalize": "characters"}))

    def clean_code(self):
        return self.cleaned_data["code"].strip().upper()


class GroupForm(StyledMixin, forms.Form):
    name = forms.CharField(label="Group name", max_length=60,
                           widget=forms.TextInput(attrs={"placeholder": "Room 3B"}))

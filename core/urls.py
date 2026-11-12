from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from . import views

urlpatterns = [
    path("", views.landing, name="landing"),

    # Account. Sign up and log in are two tabs of one page.
    path("signup/", views.account, {"tab": "signup"}, name="signup"),
    path("login/", views.account, {"tab": "login"}, name="login"),
    path("logout/", views.logout, name="logout"),
    path("password/change/", auth_views.PasswordChangeView.as_view(
        template_name="registration/simple_form.html", success_url=reverse_lazy("settings"),
        extra_context={"heading": "Change password", "button": "Save new password"}), name="password_change"),
    path("password/reset/", auth_views.PasswordResetView.as_view(
        template_name="registration/simple_form.html", email_template_name="registration/reset_email.txt",
        subject_template_name="registration/reset_subject.txt", success_url=reverse_lazy("password_reset_done"),
        extra_context={"heading": "Forgot your password?", "button": "Send reset link"}), name="password_reset"),
    path("password/reset/sent/", auth_views.PasswordResetDoneView.as_view(
        template_name="registration/simple_message.html",
        extra_context={"heading": "Check your email",
                       "body": "If that email has an account, a reset link is on its way."}),
        name="password_reset_done"),
    path("password/reset/<uidb64>/<token>/", auth_views.PasswordResetConfirmView.as_view(
        template_name="registration/simple_form.html", success_url=reverse_lazy("password_reset_complete"),
        extra_context={"heading": "Choose a new password", "button": "Save new password"}),
        name="password_reset_confirm"),
    path("password/reset/done/", auth_views.PasswordResetCompleteView.as_view(
        template_name="registration/simple_message.html",
        extra_context={"heading": "Password saved", "body": "You can log in with your new password now."}),
        name="password_reset_complete"),

    path("setup/", views.setup, name="setup"),
    path("home/", views.dashboard, name="dashboard"),
    path("alerts/<int:pk>/dismiss/", views.dismiss_alert, name="dismiss_alert"),

    path("spending/", views.expenses, name="expenses"),
    path("spending/<int:pk>/edit/", views.expense_edit, name="expense_edit"),
    path("spending/<int:pk>/delete/", views.expense_delete, name="expense_delete"),

    path("recovery/", views.recovery, name="recovery"),
    path("recovery/plan/", views.choose_plan, name="choose_plan"),

    path("allowance/", views.allowance, name="allowance"),

    path("bills/", views.bills, name="bills"),
    path("bills/<int:pk>/paid/", views.bill_paid, name="bill_paid"),
    path("bills/<int:pk>/delete/", views.bill_delete, name="bill_delete"),
    path("repayments/<int:pk>/<str:answer>/", views.repayment_answer, name="repayment_answer"),

    path("insights/", views.insights, name="insights"),

    path("dorm/", views.my_dorm, name="dorm"),
    path("dorm/shares/<int:pk>/repay/", views.share_repay, name="share_repay"),
    path("dorm/expenses/<int:pk>/delete/", views.shared_delete, name="shared_delete"),
    path("dorm/remind/<int:user_id>/", views.remind, name="remind"),

    path("settings/", views.settings_page, name="settings"),
]

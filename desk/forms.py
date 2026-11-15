from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import User
from django.core.validators import MaxLengthValidator
from .models import Customer, Invitation, Membership, Ticket, Workspace


class LoginForm(AuthenticationForm):
    username = forms.EmailField(
        label="Email", widget=forms.EmailInput(attrs={"autocomplete": "username"})
    )

    def clean_username(self):
        return self.cleaned_data["username"].strip().lower()


class ScopedTicketForm(forms.ModelForm):
    revision = forms.IntegerField(min_value=1, widget=forms.HiddenInput, required=False)

    class Meta:
        model = Ticket
        fields = [
            "customer",
            "subject",
            "description",
            "status",
            "priority",
            "assignee",
            "category",
        ]
        widgets = {"description": forms.Textarea(attrs={"rows": 6})}

    def __init__(self, *args, workspace, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.workspace = workspace
        self.fields["customer"].queryset = workspace.customers.all()
        self.fields["assignee"].queryset = workspace.memberships.filter(
            is_active=True
        ).select_related("user")
        self.fields["category"].queryset = workspace.categories.all()
        self.fields["description"].validators.append(MaxLengthValidator(20000))
        if self.instance.pk:
            self.fields["revision"].required = True
            self.fields["revision"].initial = self.instance.revision


class TicketUpdateForm(ScopedTicketForm):
    class Meta(ScopedTicketForm.Meta):
        fields = ["status", "priority", "assignee", "category"]

    def __init__(self, *args, **kwargs):
        # Parent initialization expects customer field; metadata update excludes it.
        forms.ModelForm.__init__(
            self, *args, **{k: v for k, v in kwargs.items() if k != "workspace"}
        )
        workspace = kwargs["workspace"]
        self.instance.workspace = workspace
        self.fields["assignee"].queryset = workspace.memberships.filter(
            is_active=True
        ).select_related("user")
        self.fields["category"].queryset = workspace.categories.all()
        self.fields["revision"].required = True
        self.fields["revision"].initial = self.instance.revision


class MessageForm(forms.Form):
    body = forms.CharField(
        max_length=20000,
        widget=forms.Textarea(attrs={"rows": 5, "placeholder": "Write a thoughtful response…"}),
    )
    internal = forms.BooleanField(required=False)
    revision = forms.IntegerField(min_value=1, widget=forms.HiddenInput)


class CustomerForm(forms.ModelForm):
    class Meta:
        model = Customer
        fields = ["name", "email", "phone", "company", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args, workspace, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.workspace = workspace
        self.fields["notes"].validators.append(MaxLengthValidator(10000))

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        matches = Customer.objects.filter(
            workspace=self.instance.workspace, email__iexact=email
        ).exclude(pk=self.instance.pk)
        if matches.exists():
            raise forms.ValidationError("A customer with this email already exists.")
        return email


class InvitationForm(forms.ModelForm):
    class Meta:
        model = Invitation
        fields = ["email", "role"]

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()


class MemberForm(forms.Form):
    role = forms.ChoiceField(choices=Membership.Role.choices)
    is_active = forms.BooleanField(required=False)


class WorkspaceForm(forms.ModelForm):
    class Meta:
        model = Workspace
        fields = ["name", "timezone"]

    def clean_timezone(self):
        value = self.cleaned_data["timezone"]
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise forms.ValidationError(
                "Enter a valid timezone, for example America/New_York or UTC."
            )
        return value


class AccountForm(forms.Form):
    name = forms.CharField(max_length=150)
    email = forms.EmailField(max_length=150)
    password1 = forms.CharField(label="Password", widget=forms.PasswordInput)
    password2 = forms.CharField(label="Confirm password", widget=forms.PasswordInput)

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if (
            User.objects.filter(email__iexact=email).exists()
            or User.objects.filter(username__iexact=email).exists()
        ):
            raise forms.ValidationError(
                "An account with this email already exists. Sign in instead."
            )
        return email

    def clean(self):
        data = super().clean()
        if data.get("password1") != data.get("password2"):
            self.add_error("password2", "The passwords do not match.")
        if data.get("password1"):
            user = User(
                username=data.get("email", ""),
                email=data.get("email", ""),
                first_name=data.get("name", ""),
            )
            try:
                password_validation.validate_password(data["password1"], user)
            except forms.ValidationError as error:
                self.add_error("password1", error)
        return data


class RegisterForm(AccountForm):
    workspace_name = forms.CharField(max_length=160)


class FeedbackForm(forms.Form):
    score = forms.TypedChoiceField(
        label="Your rating",
        choices=[
            (5, "5 — Excellent"),
            (4, "4 — Good"),
            (3, "3 — Okay"),
            (2, "2 — Poor"),
            (1, "1 — Very poor"),
        ],
        coerce=int,
        widget=forms.RadioSelect,
    )
    comment = forms.CharField(
        max_length=5000, required=False, widget=forms.Textarea(attrs={"rows": 4})
    )

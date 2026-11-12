# DormWise

Allowance budgeting and shared dorm bills for students. Django 5.2, server-rendered templates,
Tailwind CSS v4, GSAP for motion, SQLite.

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo        # optional: Room 3B demo data
python manage.py runserver
```

Demo login after `seed_demo`: `andrea.reyes@school.edu.ph` / `dormwise2026`
(also `paolo.santos@school.edu.ph` and `kat.lim@school.edu.ph`).

## Front-end

```bash
npm install          # installs gsap + tailwind, copies gsap into static/vendor
npm run css          # rebuild static/css/output.css
npm run css:watch    # rebuild on change
```

`static/vendor/gsap.min.js`, `ScrollTrigger.min.js` and `static/css/output.css` are committed,
so the app runs without Node. All motion lives in `static/js/app.js` and switches off under
`prefers-reduced-motion`.

## Where things are

| Path | What |
|---|---|
| `core/budget.py` | The money rules: spendable, daily limit, Smart Budget Recovery, midnight rollover |
| `core/dorm.py` | Invite codes, equal splits, repayments, alerts |
| `core/views.py`, `core/urls.py` | The 11 pages and their actions |
| `core/tests.py` | Unit tests for the math (`python manage.py test`) |
| `templates/core/` | One template per page, `templates/partials/` for the mascot and form fields |
| `static/css/input.css` | Tailwind import, brand tokens, component classes |

## Money rules (one place: `core/budget.py`)

- Spendable = money on hand - bills due before next allowance - what you owe roommates - savings.
- Daily limit = spendable at the start of the day / days left. Fixed until midnight, Philippine time.
- Overspending changes the plan from tomorrow. "Little by little" spreads it over every day left.
  "Fix it by Friday" recovers in 3 days and is refused if it would go under the essentials floor.
- Money a roommate owes you is not spendable until you confirm you received it.
- Equal splits in centavos. The payer absorbs the leftover centavo.

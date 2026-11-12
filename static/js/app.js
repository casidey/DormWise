/*
  DormWise front-end. Loaded with `defer`, after GSAP.
  1. Live previews (setup, expense, bill split, recovery plans)
  2. Small helpers (copy code, confirm, auto-submit)
  3. Motion with GSAP, only when the visitor allows it
  Every page works with this file missing: forms post, numbers come from Django.
*/
(function () {
  "use strict";

  var $ = function (selector, root) { return (root || document).querySelector(selector); };
  var $$ = function (selector, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(selector));
  };

  function peso(value) {
    var text = Math.abs(value).toLocaleString("en-PH", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return (value < 0 ? "-₱" : "₱") + text;
  }
  function number(input) {
    var value = parseFloat(input && input.value);
    return isFinite(value) ? value : 0;
  }
  function set(root, name, text) {
    $$('[data-out="' + name + '"]', root).forEach(function (el) { el.textContent = text; });
  }
  function isoDate(date) {
    return date.getFullYear() + "-" + String(date.getMonth() + 1).padStart(2, "0") + "-" + String(date.getDate()).padStart(2, "0");
  }

  /* ---------- 1. Live previews ---------- */

  // Allowance setup: spendable = amount - bills due before next allowance - owed - savings.
  function initSetup() {
    var form = $("[data-setup]");
    if (!form) return;
    var today = new Date(form.dataset.today + "T00:00:00");
    var owed = parseFloat(form.dataset.owed) || 0;
    var rows = $("[data-bill-rows]", form);
    var template = $("[data-bill-template]", form);

    function update() {
      var amount = number(form.elements.amount);
      var days = Math.max(1, parseInt(form.elements.days.value, 10) || 1);
      var savings = number(form.elements.savings);
      var floor = number(form.elements.floor);
      var next = new Date(today);
      next.setDate(next.getDate() + days);
      var nextIso = isoDate(next);

      var bills = owed;
      $$("[data-bill]", form).forEach(function (row) {
        var counts = row.dataset.due < nextIso;
        row.style.opacity = counts ? "" : "0.45";
        if (counts) bills += parseFloat(row.dataset.amount) || 0;
      });
      $$("[data-new-bill]", form).forEach(function (row) {
        var due = $('[name="bill_due"]', row).value;
        if (due && due < nextIso) bills += number($('[name="bill_amount"]', row));
      });

      var spendable = amount - bills - savings;
      var daily = Math.max(spendable, 0) / days;
      set(form, "daily", peso(daily));
      set(form, "days", String(days));
      set(form, "amount", peso(amount));
      set(form, "bills", "-" + peso(bills));
      set(form, "savings", "-" + peso(savings));
      set(form, "spendable", peso(spendable));
      set(form, "floor", peso(floor));
      $$("[data-next-date]", form).forEach(function (el) {
        el.textContent = next.toLocaleDateString("en-PH", { month: "short", day: "numeric" });
      });
      $("[data-shortfall]", form).hidden = !(amount > 0 && daily < floor);
    }

    $("[data-add-bill]", form).addEventListener("click", function () {
      var row = template.content.firstElementChild.cloneNode(true);
      rows.appendChild(row);
      $("input", row).focus();
      pop(row);
    });
    form.addEventListener("input", update);
    update();
  }

  // Expense form: within the limit, over it, or a shortfall.
  function initExpense() {
    var form = $("[data-expense]");
    if (!form) return;
    var left = parseFloat(form.dataset.left);
    var pool = parseFloat(form.dataset.pool);
    var days = parseInt(form.dataset.days, 10);
    var floor = parseFloat(form.dataset.floor);
    var notes = {};
    $$("[data-note]", form).forEach(function (el) { notes[el.dataset.note] = el; });

    function show(name) {
      Object.keys(notes).forEach(function (key) {
        var visible = key === name;
        if (visible && notes[key].hidden) pop(notes[key]);
        notes[key].hidden = !visible;
      });
    }

    function update() {
      var amount = number(form.elements.amount);
      if (amount <= 0 || form.elements.date.value !== form.dataset.today) return show(null);
      var tomorrow = days > 1 ? (pool - amount) / (days - 1) : pool - amount;
      set(form, "left", peso(left - amount));
      set(form, "over", peso(amount - left));
      set(form, "tomorrow", peso(Math.max(tomorrow, 0)));
      if (amount <= left) return show("within");
      show(tomorrow < floor ? "short" : "over");
    }
    form.addEventListener("input", update);
    update();
  }

  // Dorm split: equal shares in centavos, the payer covers the leftover.
  function initSplit() {
    var form = $("[data-split-form]");
    if (!form) return;
    var total = $('input[type="number"]', form);
    var extra = $('[data-out="extra"]', form);

    function update() {
      var boxes = $$('input[type="checkbox"]', form).filter(function (box) { return box.checked; });
      var cents = Math.round(number(total) * 100);
      if (!boxes.length) {
        set(form, "share", "Pick at least one person.");
        extra.hidden = true;
        return;
      }
      if (cents <= 0) {
        set(form, "share", "Enter the total to see each share.");
        extra.hidden = true;
        return;
      }
      var share = Math.floor(cents / boxes.length);
      var leftover = cents - share * boxes.length;
      set(form, "share", peso(share / 100) + " each, " + boxes.length + (boxes.length === 1 ? " person" : " people"));
      extra.hidden = leftover === 0;
      extra.textContent = "You cover the extra " + peso(leftover / 100) + " since you paid.";
    }
    form.addEventListener("input", update);
    form.addEventListener("change", update);
    update();
  }

  // Recovery: flip the day-by-day preview when a plan card is picked.
  function initPlans() {
    var form = $("[data-plans]");
    if (!form) return;
    var bubble = $("[data-bubble]");
    $$('input[name="plan"]', form).forEach(function (radio) {
      radio.addEventListener("change", function () {
        $$("[data-preview]", form).forEach(function (panel) {
          var active = panel.dataset.preview === radio.value;
          panel.hidden = !active;
          if (active) {
            stagger($$("[data-days] > *", panel));
            if (bubble) {
              bubble.textContent = $("[data-preview-note]", panel).textContent;
              pop(bubble);
            }
          }
        });
      });
    });
  }

  /* ---------- 2. Small helpers ---------- */

  function initHelpers() {
    $$("[data-autosubmit]").forEach(function (form) {
      form.addEventListener("change", function () { form.submit(); });
    });
    $$("[data-confirm]").forEach(function (form) {
      form.addEventListener("submit", function (event) {
        if (!window.confirm(form.dataset.confirm)) event.preventDefault();
      });
    });
    $$("[data-copy]").forEach(function (button) {
      button.addEventListener("click", function () {
        var done = function () {
          var label = button.textContent;
          button.textContent = "Copied";
          pop(button);
          setTimeout(function () { button.textContent = label; }, 1600);
        };
        if (navigator.clipboard) navigator.clipboard.writeText(button.dataset.copy).then(done, done);
        else done();
      });
    });
    $$("[data-open]").forEach(function (link) {
      link.addEventListener("click", function () {
        var target = $(link.dataset.open);
        if (target) target.open = true;
      });
    });
  }

  /* ---------- 3. Motion (GSAP) ---------- */

  var gsap = window.gsap;
  var motionOK = !!gsap && window.matchMedia("(prefers-reduced-motion: no-preference)").matches;

  // Feedback: a quick sticker "pop" when something appears or changes.
  function pop(el) {
    if (motionOK) gsap.fromTo(el, { scale: 0.92 }, { scale: 1, duration: 0.45, ease: "back.out(2.2)", clearProps: "scale" });
  }
  function stagger(items) {
    if (motionOK && items.length) {
      gsap.from(items, { y: 10, opacity: 0, duration: 0.35, ease: "power2.out", stagger: 0.035, clearProps: "transform,opacity" });
    }
  }

  function countUp(el) {
    var target = parseFloat(el.dataset.count);
    if (!isFinite(target)) return;
    var state = { value: 0 };
    gsap.to(state, {
      value: target, duration: 0.9, ease: "power2.out", delay: 0.15,
      onUpdate: function () { el.textContent = peso(state.value); },
      onComplete: function () { el.textContent = peso(target); }
    });
  }

  function animateBarya(svg) {
    var mood = svg.dataset.barya;
    // Idle bob keeps the mascot alive without pulling focus.
    gsap.to(svg, { y: -5, duration: 1.6, ease: "sine.inOut", yoyo: true, repeat: -1 });
    var eyes = $(".barya-eyes", svg);
    if (eyes) {
      gsap.to(eyes, { scaleY: 0.1, duration: 0.09, repeat: -1, yoyo: true, repeatDelay: 3.4, svgOrigin: "120 130" });
    }
    var arm = $(".barya-arm", svg);
    if (arm && mood === "happy") {
      gsap.fromTo(arm, { rotation: -8 }, { rotation: 10, duration: 0.45, ease: "sine.inOut", yoyo: true, repeat: 5, svgOrigin: "192 140", delay: 0.6 });
    }
    if (mood === "oops") {
      gsap.fromTo(svg, { rotation: -4 }, { rotation: 4, duration: 0.12, yoyo: true, repeat: 5, ease: "sine.inOut", delay: 0.5,
        onComplete: function () { gsap.set(svg, { rotation: 0 }); } });
      var sweat = $(".barya-sweat", svg);
      if (sweat) gsap.fromTo(sweat, { y: -4, opacity: 0 }, { y: 8, opacity: 1, duration: 0.9, repeat: -1, repeatDelay: 1.2, ease: "power1.in" });
    }
    if (mood === "proud") {
      gsap.fromTo($$(".barya-sparkles path", svg), { scale: 0.5, opacity: 0.3 },
        { scale: 1, opacity: 1, duration: 0.6, yoyo: true, repeat: -1, stagger: 0.2, transformOrigin: "50% 50%", ease: "sine.inOut" });
    }
  }

  function initMotion() {
    if (!motionOK) return;
    if (window.ScrollTrigger) gsap.registerPlugin(window.ScrollTrigger);

    // Page load: cards rise in reading order.
    var rise = $$("[data-rise]");
    if (rise.length) {
      gsap.from(rise, { y: 22, opacity: 0, duration: 0.6, ease: "power3.out", stagger: 0.06, clearProps: "transform,opacity" });
    }
    // Landing stickers land with a bounce.
    var pops = $$("[data-pop]");
    if (pops.length) {
      gsap.from(pops, { scale: 0.7, opacity: 0, duration: 0.7, ease: "back.out(1.8)", stagger: 0.12, delay: 0.25, clearProps: "scale,opacity" });
    }
    // Landing sections reveal as they scroll in.
    if (window.ScrollTrigger) {
      gsap.set("[data-reveal]", { y: 28, opacity: 0 });
      window.ScrollTrigger.batch("[data-reveal]", {
        start: "top 88%", once: true,
        onEnter: function (batch) {
          gsap.to(batch, { y: 0, opacity: 1, duration: 0.6, ease: "power3.out", stagger: 0.08, clearProps: "transform,opacity" });
        }
      });
      $$("[data-bars]").forEach(function (group) {
        gsap.from(group.children, { scaleY: 0, duration: 0.6, ease: "back.out(1.6)", stagger: 0.08,
          scrollTrigger: { trigger: group, start: "top 90%", once: true } });
      });
    }

    // Money figures count up so the eye lands on the number that matters.
    $$("[data-count]").forEach(countUp);

    // Day chips and list rows cascade.
    $$("[data-days]").forEach(function (group) {
      if (group.offsetParent !== null) {
        gsap.from(group.children, { scale: 0.8, opacity: 0, duration: 0.4, ease: "back.out(1.8)", stagger: 0.04, delay: 0.35, clearProps: "transform,opacity" });
      }
    });
    var rows = $$("[data-row]");
    if (rows.length) {
      gsap.from(rows, { x: -12, opacity: 0, duration: 0.4, ease: "power2.out", stagger: 0.035, delay: 0.3, clearProps: "transform,opacity" });
    }

    // Charts grow from their baseline.
    $$("[data-hbars] .hbar").forEach(function (bar, index) {
      gsap.from(bar, { scaleX: 0, duration: 0.7, ease: "power3.out", delay: 0.3 + index * 0.07 });
    });
    $$("[data-vbars] .vbar").forEach(function (bar, index) {
      gsap.from(bar, { scaleY: 0, duration: 0.7, ease: "back.out(1.4)", delay: 0.35 + index * 0.08 });
    });
    $$("[data-split] > *").forEach(function (part, index) {
      gsap.from(part, { scaleX: 0, transformOrigin: "left center", duration: 0.6, ease: "power3.out", delay: 0.3 + index * 0.12 });
    });

    $$(".barya").forEach(animateBarya);

    // Toasts slide in, and plain confirmations clear themselves.
    var toasts = $$(".toast");
    if (toasts.length) gsap.from(toasts, { y: -14, opacity: 0, duration: 0.4, ease: "power2.out", stagger: 0.08 });
    $$("[data-toast]").forEach(function (toast) {
      gsap.to(toast, { opacity: 0, height: 0, paddingTop: 0, paddingBottom: 0, marginTop: -8, borderWidth: 0,
        duration: 0.4, delay: 6, ease: "power2.in", onComplete: function () { toast.remove(); } });
    });
  }

  initSetup();
  initExpense();
  initSplit();
  initPlans();
  initHelpers();
  initMotion();
})();

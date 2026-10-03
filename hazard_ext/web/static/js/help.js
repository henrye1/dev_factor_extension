// Help text for every assumption and method. Edit the entries here; the help icons, the
// help panel and the Help page all read from this file.

import { h, openDialog } from "./ui.js";

export const METHOD_HELP = {
  1: {
    title: "Method 1: exponential decay",
    text: [
      "The tail shape is e^(−λb), where b is the bucket number. Each month the recovery rate falls by the same proportion, so the tail has a constant half-life of ln 2 ÷ λ months.",
      "λ is fitted by a log-linear regression of ln RecoveryPct on bucket number along the reference row, from FitStart to that row's last credible bucket. It can be replaced with the λ override.",
      "This is the challenger's own view of itself. It extends the steep decay seen in the observed data and adds the least recovery of the three shapes. Treat it as the independent, conservative scenario.",
      "Because the fitted tail also replaces the thin observed buckets beyond the credibility cut, a row whose late buckets were noisy but generous can end up with a lower recovery than the file gave it. That is the credibility cut doing its job, not an error.",
    ],
  },
  2: {
    title: "Method 2: power law",
    text: [
      "The tail shape is b^(−γ). The recovery rate still falls every month, but by a shrinking proportion, so the tail is heavier than the exponential and has no fixed half-life.",
      "γ is fitted the same way as λ, by regressing ln RecoveryPct on ln b over the same window, and can be replaced with the γ override.",
      "This is the second independent view: only the challenger's own data, but with the assumption that late collections persist the way long-run recovery books tend to. It sits between the exponential and the client shape.",
    ],
  },
  3: {
    title: "Method 3: reference curve shape",
    text: [
      "The tail shape is a supplied reference recovery curve: monthly cash as a share of the balance at default, by month since default. Each zip uses the curve whose label equals its category. The six built-in curves are the prototype log-normal cohort curves (11, 15, 22, 23, 25 and 44, t = 1 to 553) from the July 2026 valuation; a project can upload its own under Members and curves.",
      "Nothing about the level is borrowed. The scale is still fitted to the challenger's own last credible buckets; only the rate of decay beyond them follows the reference curve.",
      "The challenger's tail is therefore aligned to the reference curve by construction. It answers one question: what would the challenger show if we accepted that curve's tail? With the prototype curves it adds the most recovery of the three.",
      "The ALL zip has no curve of its own. Give it a curve in an override, upload a curve labelled ALL, or use method 1 or 2 for it.",
    ],
  },
};

export const CONCEPT_HELP = {
  termstep: {
    title: "TermStep and bucket",
    text: [
      "A TermStep is the age of an account at valuation, in months since default. A bucket is a calendar month after default in which cash is collected. An account valued at TermStep ts can only collect in buckets b ≥ ts.",
      "RecoveryPct(ts, b) is the cash collected in bucket b as a percentage of the balance at TermStep ts. Because the denominator is the balance at ts, the values along a row add up directly to the cumulative recovery.",
    ],
  },
  credible: {
    title: "Last credible bucket and the window",
    text: [
      "For each TermStep row, the last credible bucket is the last bucket whose opening exposure is at least MinExposure. Observed values are kept up to that bucket; beyond it the fitted tail takes over, replacing thin and noisy observations.",
      "If no bucket in a row reaches MinExposure the row falls back to its last observed bucket, so nothing is lost. The window is the last W credible buckets; the shape is scaled so that it passes through them on average.",
    ],
  },
  lgd: {
    title: "How LGD is calculated",
    text: [
      "LGD = 1 − the present value of recoveries, as a share of the balance at the TermStep. Each bucket's RecoveryPct is discounted at the file's rate for (b − ts + 1) months.",
      "The replica LGD recomputes the file's own LGD from the observed triangle and must tie out to the file to within rounding. The extended LGD adds the tail buckets up to MaxBucket.",
      "The risk suite floors each TermStep's LGD at the previous TermStep's LGD. Where that bites, the file LGD is not 1 − CumulativeSumPV; the app shows the file value, reports the gap in its own column, and measures the tie-out against 1 − CumulativeSumPV. The extended LGD is not floored.",
    ],
  },
  applied: {
    title: "Client applied recovery curves",
    text: [
      "The curves the client actually applies can be uploaded under Members and curves, one column per cohort. They are drawn on the results charts as a dashed line for comparison and never enter the calculation.",
      "On upload you state what the monthly rates are a share of. Face value: each month's cash as a share of the balance at default, which is the app's own basis. Outstanding balance: a share of what is still owed at the start of that month; the app converts it by multiplying each rate by the share still outstanding.",
      "On the RecoveryPct chart the curve is rolled forward to the chosen TermStep in the same way the app derives later rows: the rates from that month on, divided by what the curve says is still outstanding. On the LGD charts the implied LGD discounts the rolled-forward curve at the file's rate to the scenario's MaxBucket.",
      "Under Members and curves, the comparison section puts the observed data, our three fitted tails and the client's curve on one chart, on either basis. Switching to the outstanding basis shows every curve the way a client who applies rates to the outstanding balance sees it; the cumulative table below it compares our selected tail with the client's curve at fixed horizons.",
    ],
  },
  derived: {
    title: "Derived TermSteps beyond LastTS",
    text: [
      "TermSteps up to LastTS use their own observed-and-extended row. Beyond LastTS there is no row of its own, so the base row's extended cash curve is rolled forward: the balance at ts is the base balance less the cash collected between, and the remaining curve is rescaled to that balance.",
      "The validation column shows own-row LGD less derived LGD for every observed TermStep, so you can see how well one rolled-forward curve reproduces the observed rows before relying on it further out.",
    ],
  },
};

export const PARAM_HELP = {
  target_ts: {
    title: "Target TermStep",
    what: "The last TermStep in the LGD table. TermSteps beyond the observed range are derived from the base row.",
    def: "300 in the workbooks; 360 in the current scenarios.",
    effect: "Raising it adds derived rows at the end of the table; it does not change any observed row. Keep MaxBucket at least Target plus the valuation horizon, or the last rows run out of buckets.",
  },
  max_bucket: {
    title: "MaxBucket",
    what: "The last bucket to which every row is extended. Recoveries after it are not counted.",
    def: "420 in the workbooks; 480 in the current scenarios.",
    effect: "Raising it counts more tail recovery and lowers LGD slightly under the heavier shapes (power law and reference curve shape); the exponential tail is usually spent long before. Beyond the end of the reference curve (month 553 for the built-in curves, earlier for some cohorts) shape 3 adds nothing.",
  },
  horizon2: {
    title: "Valuation horizon (months)",
    what: "A second LGD counting recoveries only within this many months of each TermStep. A valuation that projects 120 months from any age sees this figure.",
    def: "120.",
    effect: "Only the horizon LGD columns change. It lets the lifetime figure be compared with what a fixed valuation window would see.",
  },
  horizon: {
    title: "Short horizon (months)",
    what: "A third LGD counting recoveries only within this many months of each TermStep, the 12-month ECL basis.",
    def: "12.",
    effect: "Only the short-horizon LGD columns change.",
  },
  method: {
    title: "Method",
    what: "Which tail shape is reported as the selected LGD. All three are always computed and shown side by side.",
    def: "3, reference curve shape.",
    effect: "Changes the selected columns, the uplift, the LGD-to-Target table and the Excel exports. See the method explanations for what each shape assumes.",
    methods: true,
  },
  client_cohort: {
    title: "Reference curve",
    what: "The curve method 3 scales its tail to, also drawn on the comparison chart. Empty means the curve whose label matches the zip's category.",
    def: "Empty.",
    effect: "A different curve changes the decay of the method 3 tail only; the level is still fitted to the zip's own data. The ALL zip has no curve of its own and needs one named here or in its override.",
  },
  min_exposure_mode: {
    title: "MinExposure basis",
    what: "Whether the credibility cut is a Rand amount or a percentage of the TermStep 1 opening exposure.",
    def: "Rand amount.",
    effect: "A percentage scales the cut to the size of each book, so one scenario can treat cohort 44 (R4.0 billion opening) and ALL (R70.6 billion) consistently.",
  },
  min_exposure: {
    title: "MinExposure (credibility cut)",
    what: "Buckets whose opening exposure is below this are treated as unobserved and replaced by the fitted tail.",
    def: "R100 million.",
    effect: "Lower it to keep more of the thin observed tail and lean less on the shape; raise it to hand over to the shape sooner. It is the main lever on how much of the noisy late data is trusted. Rows whose opening exposure is below the cut fall back to their last observed bucket.",
  },
  window: {
    title: "Window W (buckets)",
    what: "How many of the last credible buckets set the level of the tail. The shape is scaled to pass through them on average.",
    def: "12.",
    effect: "Wider is steadier and less sensitive to one odd month; narrower follows the most recent observations. It changes the level of every extended row, not the shape.",
  },
  floor: {
    title: "Hazard floor (per bucket)",
    what: "A minimum RecoveryPct applied to every extended bucket.",
    def: "0.",
    effect: "A positive floor stops the tail from decaying to nothing and lowers LGD for every row. Use it only where a minimum collection rate is a defensible assumption.",
  },
  ref_ts: {
    title: "Reference TermStep for λ / γ",
    what: "The row whose observed tail is used to fit the decay parameters λ and γ.",
    def: "1, the longest and best-populated row.",
    effect: "A later row has fewer buckets and less exposure, so the fit is noisier. Change it only when TermStep 1 is not representative.",
  },
  fit_start: {
    title: "FitStart bucket",
    what: "The first bucket in the regression that fits λ and γ. The fit runs from here to the reference row's last credible bucket.",
    def: "24, past the early hump of fresh collections.",
    effect: "Starting earlier includes the hump and flattens the fitted decay; starting later fits only the tail and needs enough credible buckets after it to be reliable. A warning appears when fewer than three points remain.",
  },
  lambda_override: {
    title: "λ override",
    what: "Replaces the fitted exponential decay with a given value. Empty means fitted.",
    def: "Empty.",
    effect: "Only the exponential tail changes. A smaller λ decays more slowly and adds more recovery. The prototype log-normal curve for cohort 44 decays at about λ ≈ 0.013 between months 96 and 300; the challenger's own fit gives about 0.053.",
  },
  gamma_override: {
    title: "γ override",
    what: "Replaces the fitted power-law exponent with a given value. Empty means fitted.",
    def: "Empty.",
    effect: "Only the power-law tail changes. A smaller γ gives a heavier tail and more recovery.",
  },
  base_ts: {
    title: "Base TermStep",
    what: "The row whose extended cash curve is rolled forward for TermSteps beyond LastTS.",
    def: "1.",
    effect: "Changes every derived row. TermStep 1 is the longest and best-populated row, so it is the usual choice.",
  },
  last_ts: {
    title: "LastTS (last own row used)",
    what: "The last TermStep that uses its own observed-and-extended row. Later TermSteps are derived from the base row. Empty means the last observed TermStep.",
    def: "Empty.",
    effect: "Lower it when late rows rest on too little exposure to be credible: for cohort 44, rows from TermStep 78 have under R100 million of opening exposure and a LastTS of 77 hands them to the rolled-forward base row. The validation column shows how far own rows and derived rows differ.",
  },
  event_type: {
    title: "EventType",
    what: "Which block of lgd_recovery.csv is used.",
    def: "Lifetime.",
    effect: "In every zip delivered so far the three blocks are identical, so this changes nothing. It exists for files where they differ.",
  },
  rate: {
    title: "Discount rate p.a.",
    what: "The annual rate used to discount recoveries, as a fraction (0.1771 means 17.71%). Empty means the rate implied by each file's own DiscountFactor.",
    def: "Empty.",
    effect: "A different rate breaks the tie-out to the file's LGD, which the app reports as a warning. Use it only to test sensitivity; the file's own rate is what the challenger used.",
  },
};

function paragraphs(list) {
  return list.map((t) => h("p", null, t));
}

export function helpContent(name) {
  if (PARAM_HELP[name]) {
    const e = PARAM_HELP[name];
    return h("div", null,
      h("p", null, e.what),
      h("dl", { class: "facts help" },
        h("div", null, h("dt", null, "Default"), h("dd", null, e.def))),
      h("h3", null, "What changing it does"),
      h("p", null, e.effect),
      e.methods ? h("div", null, [1, 2, 3].map((m) => h("div", { class: "helplink" },
        h("button", { type: "button", class: "link", onclick: () => openHelp("method" + m) }, METHOD_HELP[m].title)))) : null);
  }
  if (name.startsWith("method")) return h("div", null, paragraphs(METHOD_HELP[name.slice(6)].text));
  if (CONCEPT_HELP[name]) return h("div", null, paragraphs(CONCEPT_HELP[name].text));
  return h("p", null, "No help is written for this item yet.");
}

export function helpTitle(name) {
  if (PARAM_HELP[name]) return PARAM_HELP[name].title;
  if (name.startsWith("method")) return METHOD_HELP[name.slice(6)].title;
  if (CONCEPT_HELP[name]) return CONCEPT_HELP[name].title;
  return name;
}

// Opens the help for one item in a dialog. Works on top of an open edit dialog too.
export function openHelp(name) {
  openDialog({
    title: helpTitle(name),
    body: h("div", { class: "helpbody" }, helpContent(name),
      h("p", { class: "small" }, h("a", { href: "#/help", onclick: () => document.querySelectorAll("dialog").forEach((d) => { d.close(); d.remove(); }) }, "Open the full help page"))),
    actions: [{ label: "Close" }],
  });
}

export function helpButton(name) {
  return h("button", {
    type: "button", class: "help", "aria-label": "Help: " + helpTitle(name), title: "What does this mean?",
    onclick: (ev) => { ev.preventDefault(); ev.stopPropagation(); openHelp(name); },
  }, "?");
}

// The full help page (#/help).
export function helpPage() {
  const section = (title, items, render) => h("section", { class: "block" }, h("h2", null, title), items.map(render));
  const methods = section("The three tail methods", [1, 2, 3], (m) => h("div", { class: "helpitem", id: "method" + m },
    h("h3", null, METHOD_HELP[m].title), paragraphs(METHOD_HELP[m].text)));
  const concepts = section("How the calculation works", Object.keys(CONCEPT_HELP), (k) => h("div", { class: "helpitem", id: k },
    h("h3", null, CONCEPT_HELP[k].title), paragraphs(CONCEPT_HELP[k].text)));
  const params = section("Every assumption in a scenario", Object.keys(PARAM_HELP), (k) => h("div", { class: "helpitem", id: k },
    h("h3", null, PARAM_HELP[k].title),
    h("p", null, PARAM_HELP[k].what),
    h("p", null, h("b", null, "Default: "), PARAM_HELP[k].def),
    h("p", null, h("b", null, "Changing it: "), PARAM_HELP[k].effect)));
  return {
    trail: [{ label: "Projects", href: "#/projects" }, { label: "Help" }],
    node: h("div", { class: "helppage" },
      h("div", { class: "pagehead" }, h("div", null, h("h1", null, "Help"),
        h("div", { class: "sub" }, "What each scenario assumption does, how the three tail methods differ, and how the LGD is put together. The same text appears behind every ? icon in the app."))),
      methods, concepts, params),
  };
}

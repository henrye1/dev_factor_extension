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
      "This is the second independent view: only the challenger's own data, but with the assumption that late collections persist the way long-run recovery books tend to. It usually sits between the exponential and the log-normal.",
    ],
  },
  3: {
    title: "Method 3: log-normal",
    text: [
      "The tail shape is (1/b) × exp(−(ln b − μ)² ÷ (2σ²)): the log-normal density in the bucket number, the same functional form as the client's industry recovery curves. Recoveries rise to a peak at bucket exp(μ − σ²) and then decay, more slowly than the exponential and with a heavier tail the larger σ is.",
      "μ and σ are fitted to the challenger's own data with the same log-linear least-squares regression as λ and γ, on the same points: ln RecoveryPct + ln b is regressed on ln b and its square along the reference row from FitStart to that row's last credible bucket. Either parameter can be replaced with an override; with one given, the other is still fitted by a single regression.",
      "Nothing is borrowed from the client except the functional form. The level is scaled to the challenger's own last credible buckets like the other two shapes, so the fitted μ and σ can be compared directly with the client's parameters without using the client's curve.",
      "The fit is undefined when the tail is not concave in ln b over the regression window (the quadratic coefficient is not negative) or when fewer than three points remain. The log-normal columns are then blank, a warning explains why, and a scenario on method 3 fails for that zip with a clear message rather than falling back to another shape.",
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
  vintages: {
    title: "Vintage windows",
    text: [
      "lgd_recovery.csv is one triangle over every default vintage in the file. The same zip carries runoff_triangle.csv with the exposure of each default vintage (CohortDate) month by month, from which the app rebuilds the triangle for any set of vintages: for a TermStep and bucket it sums, over the kept vintages observed at that bucket, the exposure at the TermStep and the balances before and after the bucket.",
      "On upload the app rebuilds the triangle with every vintage and checks it against lgd_recovery.csv to within rounding. Only zips that pass can be filtered; the project page shows the vintage range of each zip. Zips uploaded before this was added hold no runoff data and must be uploaded again.",
      "A scenario chooses the vintages once: the last N years of each zip's own vintages (so one scenario stays dynamic across zips) or vintages from a month. With a filter the 'Original' LGD is the LGD of the rebuilt subset, not the file's figure, and is labelled 'LGD (vintages from …)'. The observed triangle is shallower, so more of each row is fitted tail, and a Rand MinExposure cuts credibility earlier on the smaller book; a percentage MinExposure follows the subset.",
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
    effect: "Raising it counts more tail recovery and lowers LGD slightly under the heavier shapes (power law and log-normal); the exponential tail is usually spent long before.",
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
    def: "1, exponential.",
    effect: "Changes the selected columns, the uplift, the LGD-to-Target table and the Excel exports. See the method explanations for what each shape assumes.",
    methods: true,
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
    title: "Reference TermStep for the fit",
    what: "The row whose observed tail is used to fit the decay parameters λ, γ and the log-normal μ and σ.",
    def: "1, the longest and best-populated row.",
    effect: "A later row has fewer buckets and less exposure, so the fit is noisier. Change it only when TermStep 1 is not representative.",
  },
  fit_start: {
    title: "FitStart bucket",
    what: "The first bucket in the regression that fits λ, γ, μ and σ. The fit runs from here to the reference row's last credible bucket.",
    def: "24, past the early hump of fresh collections.",
    effect: "Starting earlier includes the hump and flattens the fitted decay; starting later fits only the tail and needs enough credible buckets after it to be reliable. A warning appears when fewer than three points remain.",
  },
  lambda_override: {
    title: "λ override",
    what: "Replaces the fitted exponential decay with a given value. Empty means fitted.",
    def: "Empty.",
    effect: "Only the exponential tail changes. A smaller λ decays more slowly and adds more recovery. The challenger's own fit for cohort 44 gives about 0.053.",
  },
  mu_override: {
    title: "Log-normal μ override",
    what: "Replaces the fitted location of the log-normal shape (on ln b) with a given value. Empty means fitted. With μ given and σ empty, σ is still fitted by a single regression.",
    def: "Empty.",
    effect: "Only the log-normal tail changes. A larger μ moves the peak and the mass of recoveries to later buckets. The fitted values at the workbook defaults are about 3.04 for cohort 44 and 3.77 for cohort 22; Nutun's own curve for cohort 44 pins its m at 3.25.",
  },
  sigma_override: {
    title: "Log-normal σ override",
    what: "Replaces the fitted spread of the log-normal shape (on ln b) with a given value above 0. Empty means fitted. With σ given and μ empty, μ is still fitted by a single regression.",
    def: "Empty.",
    effect: "Only the log-normal tail changes. A larger σ gives a heavier tail and more late recovery. The fitted values at the workbook defaults are about 0.70 for cohort 44 and 0.79 for cohort 22.",
  },
  vintages: {
    title: "Vintages",
    what: "Which default vintages (CohortDate in runoff_triangle.csv) feed the recovery triangle: all of them, the last N years counted back from each zip's own latest vintage, or every vintage from a chosen month.",
    def: "All vintages, which is the file's own triangle.",
    effect: "A shorter window drops old vintages: fewer cohorts, less exposure and a shallower observed triangle, so more of each row is fitted tail. 'Last N years' resolves per zip, so the same scenario gives each zip its own start month. The run fails with a clear message on a zip whose runoff data is missing or does not reproduce its file; upload that zip again.",
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
    what: "Which block of lgd_recovery.csv (and of runoff_triangle.csv) is used.",
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

// App state, the top bar, routing between tabs, and boot.
(function (P) {
  "use strict";
  const { h } = P.ui;
  const F = P.filters;
  const TAB_LABELS = { thisweek: "This week", week: "Results", season: "Season", model: "Model", games: "Games" };
  const THEMES = ["auto", "light", "dark"];
  const THEME_KEY = "pickem-theme";

  const app = {
    data: null, state: null, sort: { key: "kickoff", dir: "asc" }, search: "",
    list: null, tierOpen: false, pushedGame: false, seasonWeek: null,
  };
  P.app = app;

  function addressFor(state) {
    return location.pathname + location.search + F.toHash(state, app.data);
  }

  app.set = function (patch, opts) {
    const push = opts && opts.push;
    if (Object.keys(patch).some((k) => k !== "game")) app.list = null;
    app.state = Object.assign({}, app.state, patch);
    if (push) { history.pushState(null, "", addressFor(app.state)); app.pushedGame = true; }
    else history.replaceState(null, "", addressFor(app.state));
    app.render();
  };

  app.openGame = (id) => app.set({ game: id }, { push: true });

  app.showList = function (title, games, strategy) {
    app.list = { title, games, strategy };
    app.render();
  };

  app.closePanel = function () {
    if (app.state.game) {
      if (app.pushedGame) { app.pushedGame = false; history.back(); }
      else app.set({ game: null });
    } else if (app.list) {
      app.list = null;
      app.render();
    }
  };

  function readTheme() {
    try { return THEMES.includes(localStorage.getItem(THEME_KEY)) ? localStorage.getItem(THEME_KEY) : "auto"; }
    catch (e) { return "auto"; }
  }

  function applyTheme(theme) {
    if (theme === "auto") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = theme;
  }

  function themeButton() {
    const current = readTheme();
    const next = THEMES[(THEMES.indexOf(current) + 1) % THEMES.length];
    return h("button", {
      class: "theme", type: "button", "aria-label": `Theme: ${current}. Switch to ${next}.`,
      onclick: () => {
        try { localStorage.setItem(THEME_KEY, next); } catch (e) { /* storage blocked: this visit only */ }
        applyTheme(next);
        app.render();
      },
    }, { auto: "◐ Auto", light: "☀ Light", dark: "☾ Dark" }[current]);
  }

  function segmented(label, options, value, onPick) {
    return h("div", { class: "seg", role: "group", "aria-label": label },
      options.map(([key, text]) => h("button", {
        type: "button", "aria-pressed": String(value === key), onclick: () => onPick(key),
      }, text)));
  }

  function tierPicker(state) {
    const summary = state.tiers.length ? state.tiers.map((t) => F.TIER_LABELS[t]).join(", ") : "All tiers";
    const details = h("details", { class: "tier-pick", open: app.tierOpen },
      h("summary", null, "Tier: " + summary + " ▾"),
      h("div", { class: "menu" }, F.TIERS.map((tier) => h("label", null,
        h("input", {
          type: "checkbox", checked: state.tiers.includes(tier),
          onchange: (e) => {
            const tiers = e.target.checked
              ? F.TIERS.filter((t) => t === tier || state.tiers.includes(t))
              : state.tiers.filter((t) => t !== tier);
            app.set({ tiers });
          },
        }),
        F.TIER_LABELS[tier]))));
    details.addEventListener("toggle", () => { app.tierOpen = details.open; });
    return details;
  }

  function topBar(view) {
    const { data, state } = view;
    return h("header", { class: "top" },
      h("div", { class: "title-row" },
        h("h1", null, `Pick'em ${data.season}`),
        state.tab !== "thisweek" && data.standings.length
          ? h("label", { class: "week-pick" }, h("span", { class: "sr" }, "Week"),
            h("select", { onchange: (e) => app.set({ week: Number(e.target.value) }) },
              data.standings.map((s) => h("option", { value: s.week, selected: s.week === state.week }, `Week ${s.week}`))))
          : null,
        themeButton()),
      h("p", { class: "updated" }, `Updated ${P.ui.fmt.time(data.generated_at)}`),
      h("div", { class: "filters" },
        segmented("Board", [["all", "Both"], ["cfb", "CFB"], ["nfl", "NFL"]], state.sport, (v) => app.set({ sport: v })),
        tierPicker(state),
        state.tab !== "thisweek"
          ? segmented("Result", [["all", "All"], ["win", "Our wins"], ["loss", "Our losses"]], state.result, (v) => app.set({ result: v }))
          : null),
      h("div", { class: "chips" }, F.chips(state).map((chip) => h("button", {
        class: "chip", type: "button", "aria-label": `Clear ${chip.label}`,
        onclick: () => app.set(F.clearFilter(state, chip.key)),
      }, chip.label, h("span", { "aria-hidden": "true" }, "✕")))),
      h("nav", { class: "tabs", role: "tablist" }, F.TABS.map((key) => h("button", {
        type: "button", role: "tab", "aria-selected": String(state.tab === key),
        onclick: () => app.set({ tab: key }),
      }, TAB_LABELS[key]))));
  }

  function notBuilt() {
    return [P.ui.card(null, h("p", { class: "empty" }, "This tab is not available."))];
  }

  function noResults() {
    return [P.ui.card(null, h("p", { class: "empty" },
      "No results imported yet. They appear after the first Tuesday results import."))];
  }

  app.render = function () {
    const root = document.getElementById("app");
    const games = F.applyFilters(app.data.games, app.state);
    const view = {
      data: app.data, state: app.state, games,
      weekGames: games.filter((g) => g.week === app.state.week),
    };
    const needsResults = app.state.tab !== "thisweek" && !app.data.standings.length;
    const tab = needsResults ? noResults : (P.tabs[app.state.tab] || notBuilt);
    const panel = P.panel ? P.panel(view) : null;
    const hadPanel = Boolean(root.querySelector(".panel"));
    root.replaceChildren(...[
      topBar(view),
      h("main", { class: "tab" + (app.state.tab === "games" ? "" : " two"), id: "tab-" + app.state.tab }, tab(view)),
      panel,
    ].filter(Boolean));
    document.body.classList.toggle("locked", Boolean(panel));
    if (panel && !hadPanel) { const close = root.querySelector(".panel .close"); if (close) close.focus(); }
  };

  function boot() {
    try {
      app.data = JSON.parse(document.getElementById("pickem-data").textContent);
      app.state = F.parseHash(location.hash, app.data);
      applyTheme(readTheme());
      addEventListener("popstate", () => {
        app.pushedGame = false;  // a drill-down list stays open under a closed game
        app.state = F.parseHash(location.hash, app.data);
        app.render();
      });
      addEventListener("keydown", (e) => { if (e.key === "Escape") app.closePanel(); });
      app.render();
      document.documentElement.dataset.boot = "ok";
    } catch (error) {
      document.documentElement.dataset.boot = "error";
      document.getElementById("app").replaceChildren(
        h("pre", { id: "boot-error" }, "The dashboard could not start: " + (error && error.stack || error)));
    }
  }

  boot();
})(globalThis.Pickem = globalThis.Pickem || {});

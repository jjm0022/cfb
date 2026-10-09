// Element builders and formatters shared by every tab. Data always enters the page as text.
(function (P) {
  "use strict";
  const SVG_NS = "http://www.w3.org/2000/svg";

  function setAttrs(el, attrs) {
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
      else el.setAttribute(key, value === true ? "" : String(value));
    }
  }

  function append(el, children) {
    for (const child of children.flat(Infinity)) {
      if (child === null || child === undefined || child === false) continue;
      el.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
  }

  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    setAttrs(el, attrs);
    append(el, children);
    return el;
  }

  function s(tag, attrs, ...children) {
    const el = document.createElementNS(SVG_NS, tag);
    setAttrs(el, attrs);
    append(el, children);
    return el;
  }

  function card(title, ...children) {
    return h("section", { class: "card" }, title ? h("h2", null, title) : null, children);
  }

  const LABELS = {
    strategy: {
      us: "Us", model: "Model", "first sheet": "First sheet", "close divergence": "Close divergence",
      favorites: "Favorites", home: "Home", "field consensus": "Field consensus",
    },
    sport: { cfb: "CFB", nfl: "NFL" },
  };

  function resultTag(result) {
    const text = { win: "W", loss: "L", push: "P" }[result];
    return text
      ? h("span", { class: "res res-" + result, title: result }, text)
      : h("span", { class: "res res-none", title: "not graded" }, "—");
  }

  const EASTERN = { timeZone: "America/New_York" };
  const kickoffFormat = new Intl.DateTimeFormat("en-US",
    { ...EASTERN, weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  const timeFormat = new Intl.DateTimeFormat("en-US",
    { ...EASTERN, weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });

  const fmt = {
    spread(x) {
      if (x === null || x === undefined) return "—";
      if (x === 0) return "PK";
      return (x > 0 ? "+" : "−") + String(Math.abs(x));
    },
    homeLine(g, value) { return `${g.home} ${fmt.spread(value)}`; },
    sideLine(g, side) {
      return side === "away" ? `${g.away} ${fmt.spread(-g.line)}` : `${g.home} ${fmt.spread(g.line)}`;
    },
    team(g, side) { return side === "home" ? g.home : side === "away" ? g.away : "—"; },
    kickoff(iso) { return kickoffFormat.format(new Date(iso)) + " ET"; },
    time(iso) { return timeFormat.format(new Date(iso)) + " ET"; },
    signed(x, digits) {
      if (x === null || x === undefined) return "—";
      const text = Math.abs(x).toFixed(digits);
      return (x > 0 ? "+" : x < 0 ? "−" : "") + text;
    },
    pct(x, digits) { return P.stats.pct(x, digits); },
  };

  // The same badges the Discord messages use.
  const TIER_BADGES = {
    strong: "🔥 Strong", lean: "✅ Lean", slight: "🎯 Slight", coinflip: "🪙 Coinflip", no_market: "⚠️ No market",
  };

  function savedLogo(data, kind, sport, team) {
    const list = data.logos && data.logos[kind] && data.logos[kind][sport];
    return Boolean(list && list.includes(team));
  }

  // With a pick side ("home" or "away"), the class that rings that team and fades the other.
  function pickClass(pick, side) {
    return pick ? (side === pick ? "picked" : "unpicked") : null;
  }

  // A saved logo (with its dark version when there is one), or a plain stand-in.
  // Only files the page was told exist are named, so there is never a broken image.
  function logo(data, sport, team, large, extra) {
    const more = (extra ? " " + extra : "");
    const size = large ? " logo-lg" : "";
    if (!savedLogo(data, "light", sport, team)) {
      return large
        ? h("span", { class: "logo logo-lg logo-badge" + more, "aria-hidden": "true" }, team)
        : h("span", { class: "logo logo-none" + more, "aria-hidden": "true" });
    }
    const src = (suffix) => `logos/${encodeURIComponent(sport)}/${encodeURIComponent(team)}${suffix}.png`;
    const dark = savedLogo(data, "dark", sport, team);
    return h("span", { class: "logo" + size + more, "aria-hidden": "true" },
      h("img", { class: dark ? "logo-light" : null, src: src(""), alt: "", loading: "lazy" }),
      dark ? h("img", { class: "logo-dark", src: src("-dark"), alt: "", loading: "lazy" }) : null);
  }

  // `pick`, when given, is the side the model took; without it the matchup is drawn plain.
  function matchup(data, g, pick) {
    const away = pickClass(pick, "away"), home = pickClass(pick, "home");
    return h("span", { class: "matchup" },
      logo(data, g.sport, g.away, false, away), h("span", { class: away }, g.away), h("span", { class: "at" }, "@"),
      logo(data, g.sport, g.home, false, home), h("span", { class: home }, g.home));
  }

  P.ui = { h, s, card, LABELS, resultTag, fmt, logo, matchup, pickClass, TIER_BADGES };
  P.tabs = P.tabs || {};
})(globalThis.Pickem = globalThis.Pickem || {});

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
    { ...EASTERN, weekday: "short", hour: "numeric", minute: "2-digit" });

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

  P.ui = { h, s, card, LABELS, resultTag, fmt };
  P.tabs = P.tabs || {};
})(globalThis.Pickem = globalThis.Pickem || {});

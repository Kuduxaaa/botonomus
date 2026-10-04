"""JavaScript installed into Botonomus' private isolated world.

The isolated world shares the DOM with the page but none of its JavaScript globals,
so page scripts can neither see nor tamper with this code.
"""

from typing import Final

HELPER: Final = r"""
(() => {
  const IMPLICIT = {
    button: 'button, input[type=button], input[type=submit], input[type=reset], input[type=image]',
    link: 'a[href], area[href]',
    textbox: 'textarea, input:not([type]), input[type=text], input[type=email], ' +
             'input[type=password], input[type=search], input[type=tel], input[type=url]',
    checkbox: 'input[type=checkbox]', radio: 'input[type=radio]',
    combobox: 'select', heading: 'h1, h2, h3, h4, h5, h6', img: 'img[alt]',
    listitem: 'li', list: 'ul, ol', table: 'table', row: 'tr', cell: 'td',
  };
  const accessibleName = (el) => {
    const label = el.getAttribute('aria-label');
    if (label) return label;
    const by = el.getAttribute('aria-labelledby');
    if (by) return by.split(/\s+/)
      .map(id => document.getElementById(id)?.innerText || '').join(' ');
    if (el.labels && el.labels.length) return Array.from(el.labels).map(l => l.innerText).join(' ');
    if (el.tagName === 'INPUT' && ['button', 'submit', 'reset'].includes(el.type)) return el.value;
    if (el.tagName === 'IMG') return el.alt;
    return el.innerText || el.getAttribute('title') || el.getAttribute('placeholder') || '';
  };
  const byRole = ({role, name, exact}) => {
    const css = `[role="${CSS.escape(role)}"]` + (IMPLICIT[role] ? ', ' + IMPLICIT[role] : '');
    const norm = (s) => s.replace(/\s+/g, ' ').trim();
    return Array.from(document.querySelectorAll(css)).filter(el => {
      const explicit = el.getAttribute('role');
      if (explicit && explicit !== role) return false;
      if (name == null) return true;
      const actual = norm(accessibleName(el));
      const wanted = norm(name);
      return exact ? actual === wanted : actual.toLowerCase().includes(wanted.toLowerCase());
    });
  };
  const resolve = (selector) => {
    if (selector.startsWith('role=')) return byRole(JSON.parse(selector.slice(5)));
    if (selector.startsWith('xpath=')) {
      const found = document.evaluate(selector.slice(6), document, null,
        XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
      return Array.from({length: found.snapshotLength}, (_, i) => found.snapshotItem(i));
    }
    if (selector.startsWith('text=')) {
      const wanted = selector.slice(5).trim().toLowerCase();
      const out = [];
      for (const el of document.querySelectorAll('body *')) {
        if (['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE'].includes(el.tagName)) continue;
        const own = Array.from(el.childNodes).filter(n => n.nodeType === 3)
          .map(n => n.textContent).join('').trim().toLowerCase();
        if (own && own.includes(wanted)) out.push(el);
      }
      return out;
    }
    return Array.from(document.querySelectorAll(selector));
  };
  const visible = (el) => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  return {resolve, visible};
})()
"""
"""Selector engine: CSS (default), ``text=``, ``xpath=`` and ``role=`` (JSON payload)."""

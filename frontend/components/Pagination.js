"use client";

import { useEffect, useState } from "react";

/**
 * Client-side pagination for a table that already holds all of its rows.
 *
 * The backend now returns the full set (no more silent 500-row / last-60 caps), so the
 * only thing left is to render it a page at a time instead of one enormous scroll.
 *
 * `usePager` keeps the page index in range when the row set changes underneath it - a
 * filter, a campaign switch, or a live refresh that adds rows. Without that, sitting on
 * page 9 and then narrowing to 20 rows would leave the table blank.
 */
export function usePager(rows, perPageDefault = 25) {
  const list = Array.isArray(rows) ? rows : [];
  const [page, setPage] = useState(1);
  const [perPage, setPerPage] = useState(perPageDefault);

  const pages = Math.max(1, Math.ceil(list.length / perPage) || 1);
  useEffect(() => {
    if (page > pages) setPage(pages);
  }, [page, pages]);

  const current = Math.min(page, pages);
  const start = (current - 1) * perPage;
  return {
    rows: list.slice(start, start + perPage),
    total: list.length,
    page: current,
    pages,
    perPage,
    start,
    end: Math.min(start + perPage, list.length),
    setPage,
    setPerPage: (n) => { setPerPage(n); setPage(1); },
  };
}

export function Pager({ pager, label = "rows" }) {
  if (!pager || pager.total === 0) return null;
  const { page, pages, total, perPage, start, end, setPage, setPerPage } = pager;
  const btn = "btn btn-secondary btn-sm";
  return (
    <div className="pager">
      <span className="pager-info">
        Showing <b>{start + 1}</b>–<b>{end}</b> of <b>{total}</b> {label}
      </span>
      <span className="pager-controls">
        <label className="pager-size">
          Per page
          <select value={perPage} onChange={(e) => setPerPage(Number(e.target.value))}>
            {[10, 25, 50, 100, 250].map((n) => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>
        </label>
        <button className={btn} disabled={page <= 1} onClick={() => setPage(1)} title="First page">« First</button>
        <button className={btn} disabled={page <= 1} onClick={() => setPage(page - 1)}>‹ Prev</button>
        <span className="pager-page">{page} / {pages}</span>
        <button className={btn} disabled={page >= pages} onClick={() => setPage(page + 1)}>Next ›</button>
        <button className={btn} disabled={page >= pages} onClick={() => setPage(pages)} title="Last page">Last »</button>
      </span>
    </div>
  );
}

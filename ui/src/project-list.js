// Which rows the «Проекты» list draws, in which order, and how many of them.
//
// Three measured complaints live in this one function:
//
//   * the list was a ribbon of junk -- 42 rows, almost all of them projects the test
//     suite and the demo scripts left behind, so the owner's real project was lost in
//     the middle of it. The rows whose files really live under a configured *visible*
//     project root are drawn by default; the rest are one click away («Показать всё»),
//     never erased from the answer;
//   * an unbounded table is unusable however good its rows are: the rows are capped at
//     a page (25) and «Показать ещё» reveals the next one, exactly as the trash does;
//   * transparency: the tab heading and the bound line keep the *real* counts, so a
//     hidden row is never a disappearance -- the backend answer stays the whole listing.
//
// This module is deliberately plain ESM JavaScript (with `project-list.d.ts` for the
// types) so the regression test in `tests/test_project_list_filter.py` can execute
// *this* rule with Node instead of re-implementing it. It has no React, no DOM and no
// network.

/**
 * How many rows one page of the list holds.
 *
 * The same bound the trash already uses (20) plus a little headroom: a desktop window
 * shows about this many rows before the owner has to scroll, and 25 is small enough that
 * a ribbon cannot re-form in one screenful.
 */
export const PROJECT_LIST_PAGE_SIZE = 25;

/**
 * @typedef {{
 *   project_id?: string,
 *   archived?: boolean,
 *   in_visible_root?: boolean,
 * }} ProjectListRow
 * @typedef {{
 *   rows: ProjectListRow[],
 *   total: number,
 *   scope: "visible-root" | "all",
 *   hidden: number,
 *   tabTotal: number,
 *   drawn: number,
 *   hasMore: boolean,
 *   pageSize: number,
 * }} ProjectListSlice
 */

/**
 * The rows one page of «Проекты» may draw.
 *
 * @param {{
 *   summaries?: readonly ProjectListRow[],
 *   archived?: boolean,
 *   showAll?: boolean,
 *   shown?: number,
 *   pageSize?: number,
 * }} [options]
 * @returns {ProjectListSlice}
 */
export function projectListRows(options) {
  const input = options ?? {};
  const all = Array.isArray(input.summaries)
    ? input.summaries.filter((row) => row !== null && typeof row === "object")
    : [];
  const total = all.length;
  const pageSize =
    typeof input.pageSize === "number" && input.pageSize > 0
      ? Math.floor(input.pageSize)
      : PROJECT_LIST_PAGE_SIZE;

  // The owner's projects: the records whose files really live in a visible project
  // root. Every other row is a project a test or a demo script left in the hidden
  // workspace.
  const owned = all.filter((row) => row.in_visible_root === true);
  // The filter is only meaningful once there is something real to show. A deployment
  // that configured no visible root at all has `in_visible_root: false` on every row,
  // and hiding everything would be a worse lie than showing everything.
  const canFilter = owned.length > 0;
  const scope = canFilter && input.showAll !== true ? "visible-root" : "all";
  const scoped = scope === "visible-root" ? owned : all;

  const archived = input.archived === true;
  const onTab = scoped.filter((row) => (row.archived === true) === archived);

  const requested =
    typeof input.shown === "number" && Number.isFinite(input.shown) && input.shown > 0
      ? Math.floor(input.shown)
      : 0;
  const limit = Math.max(pageSize, requested);
  const rows = onTab.slice(0, limit);

  return {
    rows,
    total,
    scope,
    // What the default view keeps out of sight, across both tabs: the number the
    // «Показать всё» button prints, so nothing is hidden silently.
    hidden: canFilter ? total - owned.length : 0,
    tabTotal: onTab.length,
    drawn: rows.length,
    hasMore: onTab.length > rows.length,
    pageSize,
  };
}

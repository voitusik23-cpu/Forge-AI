/**
 * The shape the list rule reads from one row. Every other field of a project summary
 * travels through untouched, so the caller keeps its own (wider) row type.
 */
export type ProjectListRow = {
  project_id?: string;
  archived?: boolean;
  /**
   * `GET /api/projects/lifecycle`'s additive fact: whether the project's files really
   * live under a configured visible project root (a folder the owner can open).
   */
  in_visible_root?: boolean;
};

export type ProjectListScope = "visible-root" | "all";

export type ProjectListSlice<Row extends ProjectListRow = ProjectListRow> = {
  /** The rows this page draws, in the order the backend returned them. */
  rows: Row[];
  /** How many projects the backend listed, hidden ones included. */
  total: number;
  /** Which set the rows come from: the owner's visible-root projects, or everything. */
  scope: ProjectListScope;
  /** How many rows the default view keeps out of sight, for «Показать всё ({n})». */
  hidden: number;
  /** How many rows the current tab holds inside the current scope. */
  tabTotal: number;
  drawn: number;
  hasMore: boolean;
  pageSize: number;
};

export type ProjectListInput<Row extends ProjectListRow = ProjectListRow> = {
  summaries?: readonly Row[];
  archived?: boolean;
  showAll?: boolean;
  shown?: number;
  pageSize?: number;
};

export const PROJECT_LIST_PAGE_SIZE: number;

export function projectListRows<Row extends ProjectListRow = ProjectListRow>(
  options?: ProjectListInput<Row>
): ProjectListSlice<Row>;

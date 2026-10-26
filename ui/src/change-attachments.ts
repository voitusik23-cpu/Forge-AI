// The owner's screenshot, as the change panel handles it.
//
// «Как рассказать Forge, что приложение сломалось?» The honest answer is a picture and
// the error text. This module holds the four pure parts of that: what counts as an image
// on the **client** side, how many, how large, and how the bytes reach the API.
//
// The server does not trust any of it: `app/projects/attachments.py` decides by magic
// bytes, enforces the same 5 MB limit and sanitises the name. These checks exist so an
// obvious mistake is refused instantly instead of travelling to the backend and back.

/** The same limit the backend enforces (`MAX_ATTACHMENT_BYTES`). */
export const MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024;

/** The same count the backend accepts (`MAX_ATTACHMENTS_PER_CHANGE`). */
export const MAX_ATTACHMENTS_PER_CHANGE = 5;

/** What the file picker offers. The server still checks the bytes. */
export const IMAGE_ACCEPT = "image/*";

/**
 * Is this file plausibly an image?
 *
 * The declared MIME type is the cheap check; a clipboard image sometimes arrives with an
 * empty type, so a known image extension is accepted too. This never *proves* the bytes
 * are an image -- that is the server's magic-byte check, and its answer is the one that
 * decides whether anything is stored.
 */
export const isImageFile = (file: File): boolean => {
  const type = String(file.type || "").toLowerCase();
  if (type.startsWith("image/")) return true;
  return /\.(png|jpe?g|gif|webp)$/i.test(String(file.name || ""));
};

/**
 * The image files of one paste/drop, in order.
 *
 * A paste of *text* yields no file and returns an empty list, so the textarea keeps its
 * normal behaviour: the owner can still paste a sentence.
 */
export const imageFilesFrom = (transfer: DataTransfer | null): File[] => {
  if (!transfer) return [];
  const files: File[] = [];
  const items = transfer.files;
  for (let index = 0; index < items.length; index += 1) {
    const file = items[index];
    if (file && isImageFile(file)) files.push(file);
  }
  return files;
};

/**
 * The raw base64 of a file, without the `data:image/png;base64,` prefix.
 *
 * The API takes base64 JSON (`POST /api/projects/{id}/attachments`) rather than
 * multipart: this surface has one body contract and no multipart dependency.
 */
export const readFileAsBase64 = (file: Blob): Promise<string> =>
  new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("the file could not be read"));
    reader.onload = () => {
      const result = String(reader.result || "");
      const comma = result.indexOf(",");
      resolve(comma >= 0 ? result.slice(comma + 1) : result);
    };
    reader.readAsDataURL(file);
  });

/** One screenshot that really reached the project. */
export type ChangeAttachment = {
  /** The project-relative path the change text names. Never an absolute host path. */
  path: string;
  /** The sanitised file name, as the chip shows it. */
  name: string;
  /** The kind the bytes really are: `png`, `jpeg`, `gif` or `webp`. */
  kind: string;
  /** How many bytes were stored. */
  bytes: number;
  /** A local object URL for the thumbnail. Revoked when the chip goes away. */
  previewUrl: string;
};

/** Does a drag carry files at all (rather than selected text)? */
export const dragHasFiles = (transfer: DataTransfer | null): boolean => {
  if (!transfer) return false;
  const types = transfer.types;
  if (!types) return false;
  for (let index = 0; index < types.length; index += 1) {
    if (types[index] === "Files") return true;
  }
  return false;
};

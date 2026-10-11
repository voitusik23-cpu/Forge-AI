"""Secret-file recognition, shared by every component that must not copy or index one.

Why this module exists
----------------------
The rule "secrets never belong in source code, Git, ordinary context, logs, or model
prompts" was already enforced in two places with **different** logic:

* ``app/understanding/scanner.py`` refused to index ``.env``, key files and credential
  JSON when it built a project snapshot;
* ``app/execution/workspace_manager.py`` copied the whole source tree into the ephemeral
  execution workspace, excluding only ``.git``, ``__pycache__`` and ``*.pyc``.

The second one therefore staged ``.env`` — with every provider key in it — into the
working directory of the process the run was about to start. A probe confirmed it:
staging a directory containing ``.env`` produced a scratch tree containing ``.env``, and
the default API execution profile admits ``python``, so any admitted command could read
every credential from its own working directory.

One recognizer, used by both, is the fix: a divergence between "what the scanner refuses
to look at" and "what the execution workspace refuses to copy" is exactly how the first
leak happened, and it would happen again.

Scope and honesty about it
--------------------------
This is a **name-based denylist**, and a denylist is never complete: a secret stored in
``notes.txt`` is not recognized by any name rule. The complete fix is a declared-input
allow-list (deny-by-default staging), which is deferred in this project and is recorded
as such. What this module guarantees is narrower and checkable: a file whose *name* marks
it as credential material is never copied into an execution workspace and never indexed
into a project snapshot.

``.env.example`` and similar templates are deliberately **not** treated as secrets: they
are tracked in Git by design (this repository tracks one), they contain no values, and
refusing them would break the workloads the staging step exists for.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

__all__ = [
    "is_secret_file",
    "is_secret_directory",
    "secret_reason",
    "SECRET_FILE_NAMES",
    "SECRET_FILE_GLOBS",
    "SECRET_FILE_EXTENSIONS",
    "SECRET_FILE_PREFIXES",
    "PUBLIC_KEY_SUFFIXES",
    "SECRET_DIRECTORY_NAMES",
    "SECRET_DIRECTORY_GLOBS",
    "TEMPLATE_SUFFIXES",
]


#: Exact file names (lower-cased) that are credential material by convention.
SECRET_FILE_NAMES = frozenset({
    # environment files. `.envrc` is a direnv file: it is a shell script whose whole
    # purpose is to export environment variables, so by function it is an env file.
    ".env",
    ".envrc",
    # cloud and service credentials
    "credentials.json",
    "credentials",          # the extensionless AWS shared-credentials file
    "service_account.json",
    "serviceaccount.json",
    "service-account.json",
    "client_secret.json",
    "client_secrets.json",
    "gcloud_credentials.json",
    "application_default_credentials.json",   # GCP Application Default Credentials
    "aws_credentials",
    "kubeconfig",
    # secret stores by conventional name
    "secrets.yaml",
    "secrets.yml",
    "secrets.json",
    "secret.yaml",
    "secret.yml",
    "secret.json",
    # infrastructure state that routinely carries secrets in clear text
    "terraform.tfstate",
    "terraform.tfstate.backup",
    # git and tooling credential files
    ".git-credentials",
    ".gitconfig",
    ".terraformrc",
    # key material and stores
    "keystore.jks",
    "keystore",
    "cacerts",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    # container / tooling credential files
    ".netrc",
    "_netrc",
    ".pgpass",
    ".my.cnf",
    ".npmrc",
    ".pypirc",
    ".htpasswd",
    "htpasswd",
    ".dockercfg",
    "secring.gpg",
    # SSH material that is not a public key: denied so a directory listing of an
    # authorized-keys set does not reach a run. `known_hosts` is deliberately absent --
    # it is public host-key data and denying it would be a false positive.
    "authorized_keys",
})

#: Name patterns (lower-cased, `fnmatch` syntax) for credential files whose name varies.
SECRET_FILE_GLOBS = (
    # Firebase / Google service-account downloads, e.g. firebase-adminsdk-abcd1.json
    "firebase-adminsdk-*.json",
    "firebase-adminsdk-*.json.enc",
    # generically named secret stores
    "secrets.*.yaml",
    "secrets.*.yml",
    "secrets.*.json",
    # Terraform state for a workspace, e.g. terraform.tfstate.d/<ws>/terraform.tfstate
    "*.tfstate",
    "*.tfstate.backup",
    # `.pem`/`.key` material that has been encrypted or archived under another extension
    "*.pem.enc",
    "*.key.enc",
    "*.p12.enc",
    "*.pfx.enc",
)

#: Extensions (lower-cased, with the dot) that carry key or certificate material.
SECRET_FILE_EXTENSIONS = frozenset({
    ".pem",
    ".key",
    ".pfx",
    ".p12",
    ".pkcs12",
    ".kdbx",
    ".jks",
    ".keystore",
    ".ppk",
    ".asc",
    ".gpg",
    ".ovpn",
})

#: Prefixes that mark generated key material. ``id_rsa`` and ``id_rsa_backup`` are
#: private; ``id_rsa.pub`` is not, and is handled by ``PUBLIC_KEY_SUFFIXES``.
SECRET_FILE_PREFIXES = ("id_rsa", "id_dsa", "id_ecdsa", "id_ed25519")

#: Suffixes that mark a **public** half of a key pair. A public key is not a credential:
#: denying it protects nothing and breaks legitimate workflows, since a run that needs to
#: verify a signature or connect with a public half would lose the file. Checked before
#: the prefix rule, so ``id_rsa.pub`` is staged while ``id_rsa`` is not.
PUBLIC_KEY_SUFFIXES = (".pub",)

#: Directories whose entire contents are credential material. A directory is pruned
#: before descent, so a nested secret cannot be reached by walking into it.
SECRET_DIRECTORY_NAMES = frozenset({
    ".ssh",
    ".aws",
    ".gnupg",
    ".kube",
    ".docker",
    ".azure",
    # Terraform keeps provider credentials and cached registry tokens under
    # `.terraform`; it is not source and nothing in a run needs it.
    ".terraform",
})

#: Directory name patterns (lower-cased, `fnmatch` syntax) pruned before descent.
SECRET_DIRECTORY_GLOBS = (
    # a directory of private keys, e.g. `keys/` holding `deploy.pem`. Denying the
    # *directory* is stronger than denying the files inside it, because the walk never
    # descends far enough to inspect them.
    "keys",
    "certs",
    "private",
    ".terraform.d",
)

#: Templates that look like secrets by name but are committed by design and hold no
#: values. Checked before the secret rules, so ``.env.example`` stays stageable.
TEMPLATE_SUFFIXES = (".example", ".sample", ".template", ".dist", ".tmpl")

#: The same markers as dot-separated segments, which is how ``.env.example.local`` is
#: recognized without enumerating every combination.
_TEMPLATE_SEGMENTS = frozenset(
    marker.lstrip(".") for marker in TEMPLATE_SUFFIXES
)


def _is_template(name: str) -> bool:
    """True when the name marks a committed, value-free template.

    ``.env.example``, ``.env.sample`` and ``.env.example.local`` are templates: they are
    tracked in Git on purpose, they carry no values, and refusing to stage them would
    break legitimate workloads. Any dot-separated segment equal to a template marker
    makes the whole name a template, which covers the ``.env.<something>.local`` shape
    without enumerating every combination.
    """

    return any(segment in _TEMPLATE_SEGMENTS for segment in name.split("."))


def is_secret_directory(name: str) -> bool:
    """True when a directory holds credential material and must not be descended into."""

    normalized = name.strip().lower()
    if normalized in SECRET_DIRECTORY_NAMES:
        return True
    return any(
        fnmatch.fnmatchcase(normalized, pattern)
        for pattern in SECRET_DIRECTORY_GLOBS
    )


def _normalize_name(file_name: str) -> str:
    """Reduce a path or name to a comparable, lower-cased file name.

    Trailing dots and spaces are removed because Windows resolves ``secret.pem.`` to
    ``secret.pem``: the name on disk is the one a child process can open, so the name to
    match against must be normalized the same way. Without this, a trailing dot was a
    one-character bypass of the extension rule. A non-breaking space is stripped by
    ``str.strip`` as well, so it cannot be used to hide a suffix either.
    """

    raw = file_name.strip()
    if not raw:
        return ""
    # Splitting is explicit so a path separator cannot make a secret look ordinary.
    name = raw.replace("\\", "/").rsplit("/", 1)[-1].strip().lower()
    # Windows ignores trailing dots and spaces in the final component.
    name = name.rstrip(". ")
    if name in (".", ".."):
        return ""
    return name


def secret_reason(file_name: str) -> str | None:
    """Return the rule that recognizes ``file_name``, or ``None`` when it is not secret.

    Accepts a bare file name or a path; only the final component is examined, normalized
    for case and for Windows' trailing dot/space handling. A reason rather than a boolean
    so a caller can report *why* it refused a file without ever reporting the file's
    contents.
    """

    name = _normalize_name(file_name)
    if not name:
        return None

    # A committed template is not a secret, even when its stem would be one.
    if _is_template(name):
        return None

    # A public key is not a credential. Checked before the prefix and glob rules so
    # `id_rsa.pub` is staged while `id_rsa` is withheld.
    if name.endswith(PUBLIC_KEY_SUFFIXES):
        return None

    if name == ".env" or name.startswith(".env."):
        return "environment_file"
    if name in SECRET_FILE_NAMES:
        return "credential_file_name"
    if any(fnmatch.fnmatchcase(name, pattern) for pattern in SECRET_FILE_GLOBS):
        return "credential_file_pattern"
    if any(name.startswith(prefix) for prefix in SECRET_FILE_PREFIXES):
        return "private_key_prefix"

    suffix = Path(name).suffix
    if suffix in SECRET_FILE_EXTENSIONS:
        return "key_material_extension"
    return None


def is_secret_file(path: str | Path) -> bool:
    """True when the path's **name** marks it as credential material.

    Only the name is consulted. Content is never read: reading a file to decide whether
    it is a secret would itself be the leak this function exists to prevent.
    """

    candidate = path if isinstance(path, Path) else Path(str(path))
    return secret_reason(candidate.name) is not None

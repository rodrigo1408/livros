#!/usr/bin/env python3
"""Portão de aprovação humana e de merge dos livros (somente biblioteca padrão).

Este arquivo é a FONTE ÚNICA da lógica de aprovação. Uma cópia idêntica é
instalada no repositório `livros` em `.github/scripts/book_gate.py` (o teste
`test_gate_copy_in_sync` garante que as cópias não divergem).

Regras:
- O hash de conteúdo cobre todos os arquivos do livro, exceto `aprovacao/`,
  `.factory/` e o próprio `revisao/manifest.json`.
- A aprovação é um comentário `/aprovar <book_id> sha256:<hash>` feito no PR
  por um login autorizado, humano (não bot), que não é o autor do PR.
- O registro de aprovação referencia o ID do comentário; o portão busca o
  comentário na API do GitHub e revalida tudo — um JSON forjado não basta.
- Qualquer alteração de conteúdo após a aprovação muda o hash e bloqueia o merge.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

EXCLUDED_TOP = {"aprovacao", ".factory"}
EXCLUDED_FILES = {"revisao/manifest.json"}
REQUIRED_FILES = (
    "ideia.md", "planejamento/briefing.json", "planejamento/plano.json", "planejamento/guia-de-estilo.md",
    "pesquisas/dossie.json", "pesquisas/referencias.md", "relatorios/qualidade.md", "relatorios/auditoria.md",
    "publicacao/manuscrito.md", "publicacao/livro.epub", "publicacao/capa.jpg", "publicacao/metadados-kdp.json",
    "publicacao/validacao.json", "publicacao/INSTRUCOES-KDP.md", "revisao/RESUMO-EXECUTIVO.md",
    "revisao/COMO-REVISAR.md", "revisao/PENDENCIAS.md", "revisao/manifest.json", ".factory/state.json",
)
TEXT_SUFFIXES = {".md", ".json", ".txt", ".jsonl", ".yml", ".yaml"}
SECRET_RE = re.compile(
    r"sk-ant-[A-Za-z0-9_\-]{8,}|\bgh[pousr]_[A-Za-z0-9]{20,}\b|\bgithub_pat_[A-Za-z0-9_]{20,}\b|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\bAKIA[0-9A-Z]{16}\b")
APPROVE_RE = re.compile(r"^/aprovar\s+(?P<book>[a-z0-9][a-z0-9\-]{2,80})\s+sha256:(?P<hash>[0-9a-f]{64})\s*$",
                        re.IGNORECASE)
TRUSTED_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}
DEFAULT_BOT_PATTERNS = ("[bot]", "github-actions", "dependabot")


# ------------------------------------------------------------------ hashing
def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def compute_manifest(book_dir: Path) -> dict:
    book_dir = Path(book_dir)
    files = []
    for p in sorted(book_dir.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(book_dir).as_posix()
        if rel.split("/", 1)[0] in EXCLUDED_TOP or rel in EXCLUDED_FILES or ".tmp" in p.name:
            continue
        files.append({"path": rel, "sha256": _sha256(p), "bytes": p.stat().st_size})
    digest = hashlib.sha256("".join(f"{f['sha256']}  {f['path']}\n" for f in files).encode()).hexdigest()
    return {"content_sha256": digest, "files": files}


# ----------------------------------------------------------------- commands
def parse_command(body: str):
    """Retorna ("aprovar", book_id, hash) | ("revisar", texto) | None."""
    text = (body or "").strip().replace("\r\n", "\n")
    first, _, rest = text.partition("\n")
    first = first.strip()
    m = APPROVE_RE.match(first)
    if m:
        return ("aprovar", m.group("book").lower(), m.group("hash").lower())
    if first.lower().startswith("/revisar"):
        instr = (first[len("/revisar"):].strip() + "\n" + rest).strip()
        return ("revisar", instr)
    return None


def is_bot(user: dict, patterns=DEFAULT_BOT_PATTERNS) -> bool:
    login = (user.get("login") or "").lower()
    return user.get("type") == "Bot" or any(p.lower() in login for p in patterns)


def verify_actor(comment: dict, pr: dict, approvers: list[str], bot_patterns=DEFAULT_BOT_PATTERNS) -> list[str]:
    """Valida quem comentou. Usado para /aprovar e /revisar."""
    errors = []
    user = comment.get("user") or {}
    login = (user.get("login") or "").lower()
    allowed = [a.strip().lower() for a in approvers if a.strip()]
    if not allowed:
        errors.append("nenhum aprovador autorizado configurado (BOOK_FACTORY_OWNERS)")
    elif login not in allowed:
        errors.append(f"'{login}' não está na lista de proprietários autorizados")
    if is_bot(user, bot_patterns):
        errors.append(f"'{login}' é um bot/automação — aprovação de agentes é proibida")
    if comment.get("author_association") not in TRUSTED_ASSOCIATIONS:
        errors.append(f"associação '{comment.get('author_association')}' não é confiável")
    pr_author = ((pr.get("user") or {}).get("login") or "").lower()
    if login and login == pr_author:
        errors.append("o autor do PR não pode aprovar a própria alteração")
    return errors


def verify_approval_comment(book_dir: Path, comment: dict, pr: dict, approvers: list[str],
                            bot_patterns=DEFAULT_BOT_PATTERNS) -> tuple[list[str], dict | None]:
    errors = verify_actor(comment, pr, approvers, bot_patterns)
    cmd = parse_command(comment.get("body", ""))
    if not cmd or cmd[0] != "aprovar":
        return errors + ["comentário não é um comando /aprovar válido"], None
    _, book_id, approved_hash = cmd
    book_dir = Path(book_dir)
    if book_dir.name != book_id:
        errors.append(f"o comando aprova '{book_id}', mas o PR contém '{book_dir.name}'")
    if pr.get("state") not in (None, "open") or pr.get("merged"):
        errors.append("o PR não está aberto")
    if not pr.get("number") or not str(comment.get("issue_url", "")).endswith(f"/issues/{pr.get('number')}"):
        errors.append("o comentário não pertence a este PR")
    try:
        manifest = json.loads((book_dir / "revisao" / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return errors + [f"manifest.json ausente ou inválido: {exc}"], None
    current = compute_manifest(book_dir)["content_sha256"]
    if manifest.get("content_sha256") != current:
        errors.append("o conteúdo atual não corresponde ao manifest.json gerado pela fábrica")
    if approved_hash != current:
        errors.append(f"hash aprovado ({approved_hash[:12]}…) difere do conteúdo atual ({current[:12]}…)")
    if errors:
        return errors, None
    record = {
        "book_id": book_id, "version": int(manifest.get("version", 0)),
        "approver": comment["user"]["login"], "approver_id": int(comment["user"]["id"]),
        "approved_at": comment.get("created_at") or datetime.now(timezone.utc).isoformat(),
        "comment_id": int(comment["id"]), "comment_url": comment.get("html_url", ""),
        "pull_request": int(pr["number"]), "head_sha": (pr.get("head") or {}).get("sha", ""),
        "content_sha256": current, "manifest_files": len(compute_manifest(book_dir)["files"]),
    }
    return [], record


# --------------------------------------------------------------------- gate
def check_structure(book_dir: Path) -> list[str]:
    errors = []
    book_dir = Path(book_dir)
    for rel in REQUIRED_FILES:
        p = book_dir / rel
        if not p.is_file() or p.stat().st_size == 0:
            errors.append(f"arquivo obrigatório ausente: {rel}")
    if not list((book_dir / "capitulos").glob("cap-*.md")):
        errors.append("nenhum capítulo em capitulos/")
    for p in book_dir.rglob("*"):
        if p.is_file() and p.suffix in TEXT_SUFFIXES and p.stat().st_size < 5_000_000:
            if SECRET_RE.search(p.read_text(encoding="utf-8", errors="ignore")):
                errors.append(f"possível segredo/credencial em {p.relative_to(book_dir).as_posix()}")
    try:
        val = json.loads((book_dir / "publicacao" / "validacao.json").read_text(encoding="utf-8"))
        if not val.get("valid"):
            errors.append("validação técnica dos arquivos editoriais reprovada (publicacao/validacao.json)")
        meta = json.loads((book_dir / "publicacao" / "metadados-kdp.json").read_text(encoding="utf-8"))
        if meta.get("auto_publish") is not False:
            errors.append("metadados devem declarar auto_publish=false")
        state = json.loads((book_dir / ".factory" / "state.json").read_text(encoding="utf-8"))
        if state.get("stage") not in ("AWAITING_HUMAN_REVIEW", "APPROVED"):
            errors.append(f"livro não está aguardando revisão humana (estágio {state.get('stage')})")
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"arquivo de controle ilegível: {exc}")
    return errors


def check_changed_files(changed: list[str]) -> tuple[list[str], str | None]:
    errors, tops = [], set()
    for f in changed:
        f = f.strip()
        if not f:
            continue
        if f.startswith(".github/") or "/" not in f:
            errors.append(f"PR de livro não pode alterar arquivos fora do diretório do livro: {f}")
            continue
        tops.add(f.split("/", 1)[0])
    if len(tops) > 1:
        errors.append(f"PR altera mais de um livro: {sorted(tops)}")
    return errors, (next(iter(tops)) if len(tops) == 1 else None)


def gate(book_dir: Path, pr: dict, approvers: list[str], fetch_comment, bot_patterns=DEFAULT_BOT_PATTERNS) -> list[str]:
    """Verificação completa antes do merge. Lista vazia = aprovado."""
    book_dir = Path(book_dir)
    errors = check_structure(book_dir)
    rec_path = book_dir / "aprovacao" / "aprovacao.json"
    if not rec_path.is_file():
        return errors + ["sem registro de aprovação humana (aguardando /aprovar do proprietário)"]
    try:
        rec = json.loads(rec_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return errors + [f"registro de aprovação inválido: {exc}"]
    current = compute_manifest(book_dir)["content_sha256"]
    if rec.get("content_sha256") != current:
        errors.append("CONTEÚDO ALTERADO APÓS A APROVAÇÃO: o hash atual difere do aprovado — nova aprovação necessária")
    if rec.get("book_id") != book_dir.name:
        errors.append("registro de aprovação pertence a outro livro")
    if int(rec.get("pull_request", -1)) != int(pr.get("number", -2)):
        errors.append("registro de aprovação pertence a outro PR")
    try:
        comment = fetch_comment(int(rec["comment_id"]))
    except Exception as exc:  # noqa: BLE001
        return errors + [f"não foi possível confirmar o comentário de aprovação na API: {exc}"]
    errors += verify_actor(comment, pr, approvers, bot_patterns)
    cmd = parse_command(comment.get("body", ""))
    if not cmd or cmd[0] != "aprovar" or cmd[1] != book_dir.name or cmd[2] != rec.get("content_sha256"):
        errors.append("o comentário de aprovação no GitHub não corresponde ao registro (livro/hash)")
    if int((comment.get("user") or {}).get("id", -1)) != int(rec.get("approver_id", -2)):
        errors.append("identidade do aprovador não confere com o comentário")
    if not str(comment.get("issue_url", "")).endswith(f"/issues/{pr.get('number')}"):
        errors.append("o comentário de aprovação não pertence a este PR")
    return errors


# ---------------------------------------------------------------- github io
def github_get(path: str) -> dict:
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    req = urllib.request.Request(f"{api}{path}", headers={
        "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
        "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}"})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - URL fixa da API
        return json.loads(resp.read().decode())


def _approvers(arg: str | None) -> list[str]:
    raw = arg if arg is not None else os.environ.get("BOOK_FACTORY_OWNERS", "")
    return [a.strip() for a in raw.split(",") if a.strip()]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("manifest")
    m.add_argument("book_dir")
    p = sub.add_parser("parse-command")
    p.add_argument("--body-file", required=True)
    v = sub.add_parser("verify-approval")
    v.add_argument("--book-dir", required=True)
    v.add_argument("--comment-json", required=True)
    v.add_argument("--pr-json", required=True)
    v.add_argument("--approvers")
    v.add_argument("--write", action="store_true", help="grava aprovacao/aprovacao.json")
    a = sub.add_parser("verify-actor")
    a.add_argument("--comment-json", required=True)
    a.add_argument("--pr-json", required=True)
    a.add_argument("--approvers")
    c = sub.add_parser("changed-files")
    c.add_argument("--list-file", required=True)
    g = sub.add_parser("gate")
    g.add_argument("--repo-root", required=True)
    g.add_argument("--pr-json", required=True)
    g.add_argument("--changed-files", required=True)
    g.add_argument("--approvers")
    g.add_argument("--summary")
    args = ap.parse_args(argv)

    if args.cmd == "manifest":
        print(json.dumps(compute_manifest(Path(args.book_dir)), indent=2))
        return 0
    if args.cmd == "parse-command":
        cmd = parse_command(Path(args.body_file).read_text(encoding="utf-8"))
        print(json.dumps({"command": cmd[0] if cmd else None, "args": list(cmd[1:]) if cmd else []}, ensure_ascii=False))
        return 0
    if args.cmd == "changed-files":
        errs, book = check_changed_files(Path(args.list_file).read_text(encoding="utf-8").splitlines())
        print(json.dumps({"errors": errs, "book_id": book}, ensure_ascii=False))
        return 1 if errs or not book else 0
    comment = json.loads(Path(args.comment_json).read_text(encoding="utf-8")) if getattr(args, "comment_json", None) else {}
    pr = json.loads(Path(args.pr_json).read_text(encoding="utf-8"))
    if args.cmd == "verify-actor":
        errs = verify_actor(comment, pr, _approvers(args.approvers))
        print(json.dumps({"errors": errs}, ensure_ascii=False))
        return 1 if errs else 0
    if args.cmd == "verify-approval":
        errs, record = verify_approval_comment(Path(args.book_dir), comment, pr, _approvers(args.approvers))
        if errs:
            print(json.dumps({"errors": errs}, ensure_ascii=False))
            return 1
        if args.write:
            out = Path(args.book_dir) / "aprovacao" / "aprovacao.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
            with open(out.parent / "historico.jsonl", "a", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(json.dumps({"errors": [], "record": record}, ensure_ascii=False))
        return 0
    if args.cmd == "gate":
        changed = Path(args.changed_files).read_text(encoding="utf-8").splitlines()
        errs, book = check_changed_files(changed)
        if book and not errs:
            repo = os.environ.get("GITHUB_REPOSITORY", "")
            errs = gate(Path(args.repo_root) / book, pr, _approvers(args.approvers),
                        lambda cid: github_get(f"/repos/{repo}/issues/comments/{cid}"))
        elif not book and not errs:
            errs = ["PR não altera nenhum livro"]
        result = {"passed": not errs, "book_id": book, "errors": errs}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.summary:
            lines = ["## Portão de aprovação do livro", "",
                     "✅ Aprovação humana válida e conteúdo inalterado." if not errs else "❌ Merge bloqueado:"]
            lines += [f"- {e}" for e in errs]
            with open(args.summary, "a", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
        return 0 if not errs else 1
    return 2


if __name__ == "__main__":
    sys.exit(main())

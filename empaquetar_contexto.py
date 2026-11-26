#!/usr/bin/env python3
"""Empaquetador de código fuente a texto estructurado para revisión con LLM.

Recorre un repositorio, filtra archivos binarios y directorios irrelevantes,
genera un árbol de directorios y consolida todo el código fuente en un único
archivo de texto plano con metadatos por archivo.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Iterable

DEFAULT_IGNORE_DIRS: frozenset[str] = frozenset({
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "env",
    "build",
    "dist",
    "node_modules",
    ".idea",
    ".vscode",
    "eggs",
    ".eggs",
})

DEFAULT_IGNORE_EXTS: frozenset[str] = frozenset({
    ".ico", ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg",
    ".exe", ".dll", ".so", ".dylib", ".pyd", ".pyc", ".pyo",
    ".zip", ".7z", ".rar", ".tar", ".gz", ".bz2", ".xz",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".ttf", ".otf", ".woff", ".woff2",
    ".db", ".sqlite", ".sqlite3", ".bin", ".dat",
})


@dataclass(frozen=True)
class FileEntry:
    """Representa un archivo de texto validado y listo para empaquetar."""
    rel_path: str
    content: str
    size_bytes: int
    line_count: int
    encoding: str


def is_binary_file(path: Path, sample_size: int = 8192) -> bool:
    """Detecta si un archivo es binario buscando bytes nulos en el encabezado."""
    try:
        with path.open("rb") as fh:
            chunk = fh.read(sample_size)
        return b"\x00" in chunk
    except OSError:
        return True


def read_text_with_fallback(path: Path) -> tuple[str, str]:
    """Lee un archivo de texto intentando UTF-8 (con/sin BOM) y respaldos Windows."""
    encodings = ("utf-8-sig", "cp1252", "latin-1")
    for enc in encodings:
        try:
            text = path.read_text(encoding=enc)
            return text, enc
        except UnicodeDecodeError:
            continue
    # Respaldo final con reemplazo explícito de caracteres inválidos
    return path.read_text(encoding="utf-8", errors="replace"), "utf-8-replace"


def collect_files(
    root: Path,
    ignore_dirs: frozenset[str],
    ignore_exts: frozenset[str],
    exclude_files: set[Path],
    max_file_bytes: int,
) -> tuple[list[FileEntry], list[tuple[str, str]]]:
    """Recorre el directorio raíz y clasifica archivos incluidos y omitidos."""
    included: list[FileEntry] = []
    skipped: list[tuple[str, str]] = []

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue

        resolved = path.resolve()
        rel_path = path.relative_to(root).as_posix()

        # Verificar exclusión explícita (por ejemplo, el propio script y el archivo de salida)
        if resolved in exclude_files:
            skipped.append((rel_path, "archivo excluido (script/salida)"))
            continue

        # Verificar directorios ignorados en cualquier nivel de la ruta relativa
        rel_parts = path.relative_to(root).parts[:-1]
        if any(part in ignore_dirs for part in rel_parts):
            continue

        # Verificar extensión binaria
        if path.suffix.lower() in ignore_exts:
            skipped.append((rel_path, f"extensión ignorada ({path.suffix.lower()})"))
            continue

        try:
            size_bytes = path.stat().st_size
        except OSError as exc:
            skipped.append((rel_path, f"error de lectura stat: {exc}"))
            continue

        if size_bytes > max_file_bytes:
            skipped.append((rel_path, f"excede límite ({size_bytes:,} bytes)"))
            continue

        if is_binary_file(path):
            skipped.append((rel_path, "contenido binario detectado"))
            continue

        content, encoding = read_text_with_fallback(path)
        line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)

        included.append(
            FileEntry(
                rel_path=rel_path,
                content=content,
                size_bytes=size_bytes,
                line_count=line_count,
                encoding=encoding,
            )
        )

    return included, skipped


def build_directory_tree(rel_paths: Iterable[str]) -> str:
    """Construye una representación visual en árbol a partir de rutas POSIX."""
    tree: dict = {}
    for rel_path in rel_paths:
        node = tree
        for part in rel_path.split("/"):
            node = node.setdefault(part, {})

    lines: list[str] = []

    def _render(subtree: dict, prefix: str = "") -> None:
        items = sorted(subtree.items(), key=lambda kv: (not bool(kv[1]), kv[0].lower()))
        for idx, (name, children) in enumerate(items):
            is_last = idx == len(items) - 1
            connector = "└── " if is_last else "├── "
            suffix = "/" if children else ""
            lines.append(f"{prefix}{connector}{name}{suffix}")
            if children:
                extension = "    " if is_last else "│   "
                _render(children, prefix + extension)

    _render(tree)
    return "\n".join(lines)


def format_with_line_numbers(content: str) -> str:
    """Prefija cada línea con su número alineado a la derecha."""
    raw_lines = content.splitlines()
    width = max(len(str(len(raw_lines))), 1)
    return "\n".join(f"{idx:>{width}} | {line}" for idx, line in enumerate(raw_lines, start=1))


def write_bundle(
    output_path: Path,
    project_name: str,
    entries: list[FileEntry],
    add_line_numbers: bool,
) -> int:
    """Escribe el archivo consolidado y retorna el tamaño final en bytes."""
    tree_str = build_directory_tree(e.rel_path for e in entries)

    with output_path.open("w", encoding="utf-8", newline="\n") as out:
        out.write(f'<project name="{project_name}" total_files="{len(entries)}">\n')
        out.write("<directory_tree>\n")
        out.write(tree_str)
        out.write("\n</directory_tree>\n\n")
        out.write("<files>\n")

        for entry in entries:
            out.write(
                f'<file path="{entry.rel_path}" '
                f'bytes="{entry.size_bytes}" '
                f'lines="{entry.line_count}" '
                f'encoding="{entry.encoding}">\n'
            )
            body = format_with_line_numbers(entry.content) if add_line_numbers else entry.content
            out.write(body)
            if body and not body.endswith("\n"):
                out.write("\n")
            out.write("</file>\n\n")

        out.write("</files>\n")
        out.write("</project>\n")

    return output_path.stat().st_size


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Consolida un proyecto multiarchivo en un único .txt para análisis con LLM."
    )
    parser.add_argument(
        "root",
        nargs="?",
        default=".",
        help="Directorio raíz del proyecto (por defecto: directorio actual).",
    )
    parser.add_argument(
        "-o", "--output",
        default="contexto_proyecto.txt",
        help="Ruta del archivo de salida (por defecto: contexto_proyecto.txt).",
    )
    parser.add_argument(
        "-n", "--line-numbers",
        action="store_true",
        help="Incluye números de línea en cada archivo (útil para referenciar cambios exactos).",
    )
    parser.add_argument(
        "--max-file-kb",
        type=int,
        default=512,
        help="Tamaño máximo individual por archivo en KB (por defecto: 512).",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    root = Path(args.root).resolve()
    output_path = Path(args.output).resolve()

    if not root.is_dir():
        print(f"Error: '{root}' no es un directorio válido.", file=sys.stderr)
        return 1

    exclude_files = {output_path, Path(__file__).resolve()}

    entries, skipped = collect_files(
        root=root,
        ignore_dirs=DEFAULT_IGNORE_DIRS,
        ignore_exts=DEFAULT_IGNORE_EXTS,
        exclude_files=exclude_files,
        max_file_bytes=args.max_file_kb * 1024,
    )

    final_bytes = write_bundle(
        output_path=output_path,
        project_name=root.name,
        entries=entries,
        add_line_numbers=args.line_numbers,
    )

    est_tokens = final_bytes // 4
    print(f"Empaquetado completado: {output_path}")
    print(f"  Archivos incluidos : {len(entries)}")
    print(f"  Archivos omitidos  : {len(skipped)}")
    print(f"  Tamaño final       : {final_bytes / 1024:.1f} KB (~{est_tokens:,} tokens estimados)")

    if skipped:
        print("\nOmitidos:")
        for rel_path, reason in skipped:
            print(f"  - {rel_path}: {reason}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
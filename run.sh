#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

if [[ -n "${PYTHON:-}" ]]; then
    python_bin="$PYTHON"
elif [[ -x .venv/bin/python ]]; then
    python_bin=.venv/bin/python
elif [[ -x .venv/Scripts/python.exe ]]; then
    python_bin=.venv/Scripts/python.exe
elif python3 --version >/dev/null 2>&1; then
    python_bin=python3
else
    python_bin=python
fi

enhance_args=()
date_args=()
paper_args=()
notify_args=()
while (($#)); do
    case "$1" in
        --dry-run) enhance_args+=(--dry-run); notify_args+=(--dry-run); shift ;;
        --limit-papers|--limit-news|--max-filter-candidates)
            if (($# < 2)) || [[ ! "$2" =~ ^[0-9]+$ ]]; then
                echo "Expected a nonnegative integer after $1" >&2
                exit 2
            fi
            enhance_args+=("$1" "$2")
            if [[ "$1" == "--limit-papers" ]]; then paper_args=(--limit-papers "$2"); fi
            shift 2 ;;
        --date)
            if (($# < 2)); then echo "Missing date after --date" >&2; exit 2; fi
            date_args=(--date "$2")
            shift 2 ;;
        -h|--help)
            echo "Usage: ./run.sh [--dry-run] [--date YYYY-MM-DD] [--limit-papers N] [--limit-news N] [--max-filter-candidates N]"
            echo "Notifications default to off. See: python -m to_web.notify --help"
            exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

export PYTHONUTF8=1
day=$("$python_bin" -m digest_common "${date_args[@]}")
"$python_bin" -m daily_arxiv.fetch_arxiv --date "$day"
"$python_bin" -m daily_news.fetch_news --date "$day"
"$python_bin" -m ai.enhance --date "$day" "${enhance_args[@]}"
"$python_bin" -m ai.enrich_papers --date "$day" "${paper_args[@]}"
"$python_bin" -m ai.enrich_news --date "$day"
"$python_bin" -m ai.make_infographic --date "$day"
"$python_bin" -m to_web.build_index
"$python_bin" -m to_web.notify --date "$day" "${notify_args[@]}" || echo "Warning: notification step failed" >&2

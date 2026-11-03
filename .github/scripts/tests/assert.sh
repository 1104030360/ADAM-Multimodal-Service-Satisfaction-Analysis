# shellcheck shell=bash
# Minimal assertions for the workflow script tests (sourced). A case starts with begin NAME; failures are
# counted and reported by end_tests. Captured output is shown behind "    | ", so that a captured workflow
# command (::error:: and the like) is never acted on in the job log.

TESTS_RUN=0
TESTS_FAILED=0
CASE=
# A command failing outside an assertion stops the test file (set -e); say where.
set -E
trap 'echo "FAIL [$CASE] unexpected error at line $LINENO"' ERR

begin() {
  CASE=$1
  TESTS_RUN=$((TESTS_RUN + 1))
}

flunk() {
  echo "FAIL [$CASE] $1"
  TESTS_FAILED=$((TESTS_FAILED + 1))
}

show() { # FILE
  sed 's/^/    | /' "$1" 2>/dev/null || true
}

assert_eq() { # ACTUAL EXPECTED WHAT
  if [[ $1 != "$2" ]]; then
    flunk "$3: expected [$2], got [$1]"
  fi
}

assert_contains() { # FILE TEXT
  if ! grep -qF -- "$2" "$1" 2>/dev/null; then
    flunk "$(basename "$1") does not contain [$2]"
    show "$1"
  fi
}

assert_not_contains() { # FILE TEXT
  if grep -qF -- "$2" "$1" 2>/dev/null; then
    flunk "$(basename "$1") contains [$2]"
    show "$1"
  fi
}

end_tests() { # NAME
  if ((TESTS_FAILED > 0)); then
    echo "$1: $TESTS_FAILED failed checks in $TESTS_RUN cases"
    exit 1
  fi
  echo "$1: all $TESTS_RUN cases passed"
}

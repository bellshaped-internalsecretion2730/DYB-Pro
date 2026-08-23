# Upstream pull-request archive

DYB Pro preserves the original ForesterKanez Git history while using these remotes:

- `origin`: `https://github.com/yacine-baghli/DYB-Pro.git`
- `upstream`: `https://github.com/DAKSHPATELL/ForesterKanez.git`

GitHub pull-request objects cannot be transferred between repositories with their native authorship
and timestamps. The 30 closed upstream pull requests were therefore recreated in DYB-Pro using their
exact historical base and head commits. Their original descriptions and discussions were copied with
author, date, and source-link attribution.

- Upstream PR `N` maps to DYB-Pro PR `N + 3` (upstream #1-30 map to DYB-Pro #4-33).
- The 29 originally merged PRs are merged on isolated historical base branches.
- Upstream PR #2 remains closed without merge, matching its original state.
- The temporary `history/upstream-pr-*` and `archive/upstream-pr-*` branches were deleted after
  verification so GitHub does not show misleading "Compare & pull request" banners.

All original refs remain recoverable from the `upstream` remote. The temporary destination branches
can be rebuilt from the closed PR refs if needed.

## Recreated review pull requests

The historical variants explicitly selected for review are visible as draft pull requests in
DYB-Pro:

- [PR #1 - recovered bati-1 molecular research workbench](https://github.com/yacine-baghli/DYB-Pro/pull/1)
- [PR #2 - recovered bati-3 lean protein workspace](https://github.com/yacine-baghli/DYB-Pro/pull/2)
- [PR #3 - recovered bati-5 selection-aware protein agent](https://github.com/yacine-baghli/DYB-Pro/pull/3)

PR #1 uses `pr/recovered-upstream-bati-1` because the original branch had no common ancestor with
current `main`. Its bridge commit preserves the original tip as a second parent and reproduces its
tree exactly. PRs #2 and #3 use the exact upstream heads directly. Their required remote heads,
`archive/upstream-bati-3` and `archive/upstream-bati-5`, remain published. The unused bati-1, bati-2,
bati-4, and bati-6 archive branches were removed from `origin` to avoid comparison banners and remain
recoverable from `upstream`.

Original pull requests remain browsable at:
`https://github.com/DAKSHPATELL/ForesterKanez/pulls?q=is%3Apr`

Recreated closed pull requests are browsable at:
`https://github.com/yacine-baghli/DYB-Pro/pulls?q=is%3Apr+is%3Aclosed`

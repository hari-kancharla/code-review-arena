# Third-party notices — RealFix Seed v0

This benchmark pack vendors source and test files from third-party open-source
projects so that each case is a complete, runnable reverse-review snapshot. The
redistributed files are reproduced under the upstream licenses in effect at the
pinned commits below. The full upstream license text for each project is included
in `licenses/`. This document preserves the notices accompanying the redistributed
files; it is a redistribution record, not legal advice.

Each case vendors the selected source tree at the **buggy** commit (as `after/`)
and at the **fixed** commit (as `before/`), and the selected test tree at the
fixed commit (as `tests/`). The exact selectors and changed paths for each case
are recorded in `benchmark_sources/realfix_seed_v0/<case-id>/evidence.yaml`.

## attrs_frozen_error_message_001

- Project: attrs
- Source repository: https://github.com/python-attrs/attrs
- License: MIT (`licenses/attrs-MIT.txt`)
- Buggy commit: `eccd966d80aff5196efc959316961cfa780439f9`
- Fixed commit: `ce89f5d11feb0805da9ed10bb165238cc959f1bb`
- Included content: the `src/attr` source tree (buggy and fixed) and the `tests`
  tree at the fixed commit. Changed source path: `src/attr/exceptions.py`.

## click_shared_default_precedence_001

- Project: Click
- Source repository: https://github.com/pallets/click
- License: BSD-3-Clause (`licenses/click-BSD-3-Clause.txt`)
- Buggy commit: `6a1c0d077311f180b356965914e2de5b9e0fdb44`
- Fixed commit: `1c20dc6e724cd5625faaa17b715ba928d44c08bf`
- Included content: the `src/click` source tree (buggy and fixed) and the `tests`
  tree at the fixed commit. Changed source path: `src/click/core.py`.

## packaging_marker_extra_normalization_001

- Project: packaging
- Source repository: https://github.com/pypa/packaging
- License: Apache-2.0 OR BSD-2-Clause (`licenses/packaging-Apache-2.0.txt`,
  `licenses/packaging-BSD-2-Clause.txt`; the upstream dual-license notice is
  reproduced in `licenses/packaging-LICENSE.txt`)
- Buggy commit: `8a805e3baac2d71958ec0d0beffbe4d51fd5795f`
- Fixed commit: `1c09ddf30b79428c21aec180f52e10f6dedc1d8a`
- Included content: the `src/packaging` source tree (buggy and fixed) and the
  `tests` tree at the fixed commit. Changed source path:
  `src/packaging/markers.py`.

## packaging_name_validation_newline_001

- Project: packaging
- Source repository: https://github.com/pypa/packaging
- License: Apache-2.0 OR BSD-2-Clause (`licenses/packaging-Apache-2.0.txt`,
  `licenses/packaging-BSD-2-Clause.txt`; the upstream dual-license notice is
  reproduced in `licenses/packaging-LICENSE.txt`)
- Buggy commit: `033854a05229074ddb191d67da1f8e0165e665da`
- Fixed commit: `258202ed7f796bdb8a65252a66c3fbd3e69e97f6`
- Included content: the `src/packaging` source tree (buggy and fixed) and the
  `tests` tree at the fixed commit. Changed source path:
  `src/packaging/utils.py`.

## rich_table_padding_width_001

- Project: Rich
- Source repository: https://github.com/Textualize/rich
- License: MIT (`licenses/rich-MIT.txt`)
- Buggy commit: `fe55a131c2780fa856464ad04d7d6dc8a1079b72`
- Fixed commit: `1c5e03eb32020011f5b13174e186c588d09d749c`
- Included content: the `rich` source tree (buggy and fixed) and the `tests` tree
  at the fixed commit. Changed source path: `rich/table.py`.

The original per-file copyright and SPDX notices present in the upstream files are
retained as-is in the vendored `before/`, `after/`, and `tests/` trees.

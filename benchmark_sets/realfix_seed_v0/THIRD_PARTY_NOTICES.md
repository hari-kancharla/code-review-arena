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

## idna_invalid_alabel_001

- Project: idna
- Source repository: https://github.com/kjd/idna
- License: BSD-3-Clause (`licenses/idna-BSD-3-Clause.txt`)
- Buggy commit: `c3383c97b3fffd8aa73aaefd16baf9c6da1e9f4e`
- Fixed commit: `4fdcc18d1eb214b35e16c372e8682fb8b8a52e11`
- Included content: the `idna` source tree (buggy and fixed) and the `tests` tree at the fixed commit. Changed source path: `idna/core.py`.
## idna_non_ascii_bytes_encode_001

- Project: idna
- Source repository: https://github.com/kjd/idna
- License: BSD-3-Clause (`licenses/idna-BSD-3-Clause.txt`)
- Buggy commit: `522c0ebe02d8bd09039dd593e7c152ab5a1d26dd`
- Fixed commit: `e00ed2854c5203be201940f4029a747684594409`
- Included content: the `idna` source tree (buggy and fixed) and the `tests` tree at the fixed commit. Changed source path: `idna/core.py`.
## idna_non_string_input_001

- Project: idna
- Source repository: https://github.com/kjd/idna
- License: BSD-3-Clause (`licenses/idna-BSD-3-Clause.txt`)
- Buggy commit: `7e6df7196e6396b5b84b9530eab8272b5ad51898`
- Fixed commit: `0f4a28d88f8cce54269f0b6a42edf5e6a5424319`
- Included content: the `idna` source tree (buggy and fixed) and the `tests` tree at the fixed commit. Changed source path: `idna/core.py`.
## idna_unknown_codepoint_joiner_001

- Project: idna
- Source repository: https://github.com/kjd/idna
- License: BSD-3-Clause (`licenses/idna-BSD-3-Clause.txt`)
- Buggy commit: `1d365e17e10d72d0b7876316fc7b9ca0eebdd38d`
- Fixed commit: `b0d8f3c45d83b8b9bce0975a59f0c8ab6645694c`
- Included content: the `idna` source tree (buggy and fixed) and the `tests` tree at the fixed commit. Changed source path: `idna/core.py`.
## installer_path_traversal_001

- Project: installer
- Source repository: https://github.com/pypa/installer
- License: MIT (`licenses/installer-MIT.txt`)
- Buggy commit: `504fa8f980641c82868af834f5d30b485ad8a902`
- Fixed commit: `2eccd66b344de24ee7acc6fc01741a8aa2713f05`
- Included content: the `src/installer` source tree (buggy and fixed) and the `tests` tree at the fixed commit. Changed source path: `src/installer/destinations.py`.
## installer_unbound_executable_001

- Project: installer
- Source repository: https://github.com/pypa/installer
- License: MIT (`licenses/installer-MIT.txt`)
- Buggy commit: `de073ce0d45b9249e2b008df5ac391245e89a283`
- Fixed commit: `6c3118d04e9a279f8f5b972ba797387451c7a6b4`
- Included content: the `src/installer` source tree (buggy and fixed) and the `tests` tree at the fixed commit. Changed source path: `src/installer/_core.py`.
## more_itertools_chunked_even_001

- Project: more-itertools
- Source repository: https://github.com/more-itertools/more-itertools
- License: MIT (`licenses/more-itertools-MIT.txt`)
- Buggy commit: `c0780fbbba9655d36de09b872981ffd4a90eb120`
- Fixed commit: `49a4b3c94b0d71cc4576df3df9ca90197b5ec9fc`
- Included content: the `more_itertools` source tree (buggy and fixed) and the `tests` tree at the fixed commit. Changed source path: `more_itertools/more.py`.
## more_itertools_last_reversed_none_001

- Project: more-itertools
- Source repository: https://github.com/more-itertools/more-itertools
- License: MIT (`licenses/more-itertools-MIT.txt`)
- Buggy commit: `c834d6e4a0c4280b7b7750cb0de8dd8acb3d4c2c`
- Fixed commit: `cca32949f12d473fd823e37a5530c30d2faa1332`
- Included content: the `more_itertools` source tree (buggy and fixed) and the `tests` tree at the fixed commit. Changed source path: `more_itertools/more.py`.
## more_itertools_numeric_range_reversed_empty_001

- Project: more-itertools
- Source repository: https://github.com/more-itertools/more-itertools
- License: MIT (`licenses/more-itertools-MIT.txt`)
- Buggy commit: `247e15b3a489d5805375c95dfa79486c9bd0eb1b`
- Fixed commit: `edb3346f835ca917efbfda5e2d6664ab952da369`
- Included content: the `more_itertools` source tree (buggy and fixed) and the `tests` tree at the fixed commit. Changed source path: `more_itertools/more.py`.
## more_itertools_split_after_maxsplit_001

- Project: more-itertools
- Source repository: https://github.com/more-itertools/more-itertools
- License: MIT (`licenses/more-itertools-MIT.txt`)
- Buggy commit: `6793bd3e4ed15318746ed2511733f12a9932eb64`
- Fixed commit: `9245cd04c043d0d646497934df72549943d5f868`
- Included content: the `more_itertools` source tree (buggy and fixed) and the `tests` tree at the fixed commit. Changed source path: `more_itertools/more.py`.
## more_itertools_split_before_empty_001

- Project: more-itertools
- Source repository: https://github.com/more-itertools/more-itertools
- License: MIT (`licenses/more-itertools-MIT.txt`)
- Buggy commit: `c7e73ffbf9c7e15969f9ed301d0431770061ab90`
- Fixed commit: `2e81a562fbaccc996c19c069090a53f52ec894fe`
- Included content: the `more_itertools` source tree (buggy and fixed) and the `tests` tree at the fixed commit. Changed source path: `more_itertools/more.py`.
## more_itertools_windowed_zero_n_001

- Project: more-itertools
- Source repository: https://github.com/more-itertools/more-itertools
- License: MIT (`licenses/more-itertools-MIT.txt`)
- Buggy commit: `e4d2a4a2a97246a73856754b2c4866d7f41d4875`
- Fixed commit: `71b46b06fb48abcd2f7a26d74c148a650d340386`
- Included content: the `more_itertools` source tree (buggy and fixed) and the `tests` tree at the fixed commit. Changed source path: `more_itertools/more.py`.
## packaging_direct_url_at_in_password_001

- Project: packaging
- Source repository: https://github.com/pypa/packaging
- License: Apache-2.0 OR BSD-2-Clause (`licenses/packaging-Apache-2.0.txt, licenses/packaging-BSD-2-Clause.txt; the upstream dual-license notice is reproduced in licenses/packaging-LICENSE.txt`)
- Buggy commit: `28c299e8a823600dd66d4adeb7c7cc98e11089d2`
- Fixed commit: `08bb047794f4e70b157dacef4538b3a6e3492743`
- Included content: the `src/packaging` source tree (buggy and fixed) and the `tests` tree at the fixed commit. Changed source path: `src/packaging/direct_url.py`.
## packaging_empty_project_name_001

- Project: packaging
- Source repository: https://github.com/pypa/packaging
- License: Apache-2.0 OR BSD-2-Clause (`licenses/packaging-Apache-2.0.txt, licenses/packaging-BSD-2-Clause.txt; the upstream dual-license notice is reproduced in licenses/packaging-LICENSE.txt`)
- Buggy commit: `fb82782df51f9a33bf9d2a489361a3784bb739ff`
- Fixed commit: `84833cc16be84bc7f1d9f64e2818afe0dd48aade`
- Included content: the `src/packaging` source tree (buggy and fixed) and the `tests` tree at the fixed commit. Changed source path: `src/packaging/utils.py`.
## packaging_infinity_self_comparison_001

- Project: packaging
- Source repository: https://github.com/pypa/packaging
- License: Apache-2.0 OR BSD-2-Clause (`licenses/packaging-Apache-2.0.txt, licenses/packaging-BSD-2-Clause.txt; the upstream dual-license notice is reproduced in licenses/packaging-LICENSE.txt`)
- Buggy commit: `4339d3a0028348e21863664e6022e3cff20c3411`
- Fixed commit: `f8f16338e10d5d509ea2a29e0f0cf56baf4be565`
- Included content: the `src/packaging` source tree (buggy and fixed) and the `tests` tree at the fixed commit. Changed source path: `src/packaging/_structures.py`.
## packaging_license_empty_parens_001

- Project: packaging
- Source repository: https://github.com/pypa/packaging
- License: Apache-2.0 OR BSD-2-Clause (`licenses/packaging-Apache-2.0.txt, licenses/packaging-BSD-2-Clause.txt; the upstream dual-license notice is reproduced in licenses/packaging-LICENSE.txt`)
- Buggy commit: `f89652be562e2acd45d4def18977fd9057937c38`
- Fixed commit: `2680259b4fa88885962e1b6f1cca9d92a3e605ca`
- Included content: the `src/packaging` source tree (buggy and fixed) and the `tests` tree at the fixed commit. Changed source path: `src/packaging/licenses/__init__.py`.
## packaging_nested_extra_normalization_001

- Project: packaging
- Source repository: https://github.com/pypa/packaging
- License: Apache-2.0 OR BSD-2-Clause (`licenses/packaging-Apache-2.0.txt, licenses/packaging-BSD-2-Clause.txt; the upstream dual-license notice is reproduced in licenses/packaging-LICENSE.txt`)
- Buggy commit: `349abfad0688f42eb835ed8a10380d6cbf6940e7`
- Fixed commit: `07265129295b4b95b9143b50e3ce4709f31a8c49`
- Included content: the `src/packaging` source tree (buggy and fixed) and the `tests` tree at the fixed commit. Changed source path: `src/packaging/markers.py`.
## packaging_normalized_name_double_hyphen_001

- Project: packaging
- Source repository: https://github.com/pypa/packaging
- License: Apache-2.0 OR BSD-2-Clause (`licenses/packaging-Apache-2.0.txt, licenses/packaging-BSD-2-Clause.txt; the upstream dual-license notice is reproduced in licenses/packaging-LICENSE.txt`)
- Buggy commit: `283811bffee5da95bda6d5332cb88f8b231187a2`
- Fixed commit: `ba17fcea2367a70cba21d6bfb0931ae476ee8583`
- Included content: the `src/packaging` source tree (buggy and fixed) and the `tests` tree at the fixed commit. Changed source path: `src/packaging/utils.py`.
## tomli_text_mode_load_001

- Project: tomli
- Source repository: https://github.com/hukkin/tomli
- License: MIT (`licenses/tomli-MIT.txt`)
- Buggy commit: `e4da05c35a41a4d53fce7af292b94506b1ea68a7`
- Fixed commit: `8b962e13490a569d4aab90076451def23ed6c6d8`
- Included content: the `src/tomli` source tree (buggy and fixed) and the `tests` tree at the fixed commit. Changed source path: `src/tomli/_parser.py`.
The original per-file copyright and SPDX notices present in the upstream files are
retained as-is in the vendored `before/`, `after/`, and `tests/` trees.

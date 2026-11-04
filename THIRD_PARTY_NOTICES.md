# Third-party notices

This repository is licensed under the Apache License 2.0 (`LICENSE`). It includes code from the
projects below, under their own licenses.

## pgledger

- Source: https://github.com/pgr0ss/pgledger
- Pinned commit: `d591b7693a89ddcdb527c2e59f4f702983235f69` (tag `v0.8.0`, committed 2026-10-08)
- Copied verbatim: `pgledger.sql`, into `db/migrations/20261009000001_ledger_core.sql`
- Check: `python scripts/check_pgledger_verbatim.py` (CI runs it in the `migrate` job)

```
MIT License

Copyright (c) 2025 Paul Gross

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## scoville/pgsql-ulid (vendored inside pgledger)

- Source: https://github.com/scoville/pgsql-ulid, as vendored in pgledger at
  `vendor/scoville-pgsql-ulid/` at the pgledger commit above
- Copied verbatim: `ulid-to-uuid.sql` and `uuid-to-ulid.sql`, into
  `db/migrations/20261009000001_ledger_core.sql`, ahead of `pgledger.sql`

```
BSD 3-Clause License

Copyright (c) 2021, Scoville
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its
   contributors may be used to endorse or promote products derived from
   this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

## IBM Plex Sans (the PDFs' font)

- Source: IBM Plex Sans 3.201, Regular and Bold, as Google Fonts builds them, from the npm
  package `@expo-google-fonts/ibm-plex-sans` 0.4.1
  (https://registry.npmjs.org/@expo-google-fonts/ibm-plex-sans/-/ibm-plex-sans-0.4.1.tgz).
  Upstream: https://github.com/IBM/plex
- Copied verbatim: `400Regular/IBMPlexSans_400Regular.ttf` and `700Bold/IBMPlexSans_700Bold.ttf`
  into `src/pms/fonts/IBMPlexSans-Regular.ttf` and `src/pms/fonts/IBMPlexSans-Bold.ttf`, and the
  package's `LICENSE_FONT` into `src/pms/fonts/OFL.txt`
- SHA-256: Regular `8685116dfbcfa639621631c4af0ad62e52ddeb9979acbec0007ccef2e4801a27`, Bold
  `cf74841e461235ac7205369329b14fd13a126af4c93927f8b287cad9aa6ffaab`, license
  `7e6b2818edbd8f6a01ae80641cc8f16a51080d08fb4e532be3a0b6f74adb07da`
- License: SIL Open Font License 1.1 (`src/pms/fonts/OFL.txt`), Copyright © 2017 IBM Corp. with
  Reserved Font Name "Plex". The PDFs embed subsets of the fonts; they aren't sold on their own.
- Check: `tests/test_pdf_fonts.py` pins the three files' SHA-256.

# Remnawave contract lock

- Remnawave Panel target: **3.4.4**
- Matching official `@remnawave/backend-contract`: **3.4.15**
- OpenAPI source: `https://cdn.docs.rw/docs/openapi.json`, the URL configured
  by the official `https://docs.rw/api/` page when acquired for this migration.
- Original downloaded JSON SHA-256:
  `BEBC345543B82C66EE1F956333E65CDDFB8AEC46099BF4427DE38FB43DF69396`
- Formatted repository JSON SHA-256:
  `63F9861C4AEF96EF47B684E120A13F4F9602F6C6F0BB24756BFDFB4C81867FBB`
- npm package integrity for `@remnawave/backend-contract@3.4.15`:
  `sha512-yJUkJn6TCVGWTF2IYIV5Mury7pph3n+VeWt5apH3A3Ka8K9zblYoiMxOLJv99H01I+QEBVlgxb1cMm4QIVrezw==`

The downloaded OpenAPI reports `info.version: 3.4.4`. The repository copy at
`docs/remnawave-openapi.json` contains the same JSON data, formatted with two
spaces for review. The official [SDK version table](https://docs.rw/sdk/typescript-sdk/)
maps Panel 3.4.4 to contract 3.4.15. The official backend tag is
[`3.4.4`](https://github.com/remnawave/backend/releases/tag/3.4.4).
The pinned npm archive was also inspected: `GetUserByIdCommand` declares
`userId` as a number, and `BulkUpdateUsersCommand` declares
`userIds` as an array of numbers (1–500).

The public documentation URL can change to a later Panel release. Check its
reported version before refreshing this vendored file. A future version change
must update the runtime client and contract tests in the same change.

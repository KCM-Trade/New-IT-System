# tags / user_tags — CRM tags

- `tags` (~500 rows): `id`, `tag` (unique name), `categoryId`.
- `user_tags` (~233K rows): `userId` (→ users.id), `tagId` (→ tags.id), `createdAt`.
  Indexed on `userId` and `tagId`; `(tagId, userId)` unique.
- Join: `user_tags ut JOIN tags tg ON tg.id = ut.tagId`.
- CRM tags are what CRM staff put on a client; they are not the risk engine's behaviour tags.
- `get_client_overview` already returns a client's CRM tag names (`crm_tags`); use SQL only when you
  need `categoryId` or the tagging date, or to find all clients carrying a tag.
- Category meanings used elsewhere (staff code tags, venue tags) are in the `ib-and-rebate` skill.

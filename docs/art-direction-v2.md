# 月下 · 第二版视觉设计

生成方式：内置 image_gen 工具，2026-10-05。原始生成文件保留在 Codex generated_images，项目使用的最终素材已复制到 static/img/moonlight。

视觉方向：手绘悬疑卡牌。深靛蓝与梅紫构成夜色，月白与琥珀负责阅读层级和行动反馈。村庄场景、人像、身份卡使用统一画风；普通人像与真实身份无关，身份插画仅用于本人、公开板子说明或结算。

## 最终素材与用途

- `static/img/moonlight/village.png`：大厅、登录和对局场景。
- `static/img/moonlight/townspeople.png`：4×3 人物肖像图集；CSS 按座位裁切，不传递隐藏身份。
- `static/img/moonlight/roles.png`：3×2 身份插画图集；狼人、平民、预言家、女巫、猎人、白痴。

## 界面落地

- `static/theme-v2.css` 为完整的新主题；旧 `style.css` 保留供比较。
- 大厅使用村庄主视觉、人物预览和身份插画说明；对局采用圆桌与发言两栏，月白发言卡片承载主要阅读内容。
- 普通头像按座位选择，与玩家身份无关。身份插画仅在本人身份卡、公开板子和结束后的复盘出现。
- 手机以两排显示全部十二个座位；行动区固定在发言区底部，头像目标卡先选择、再确认。
- 推理簿为独立抽屉，支持键盘焦点循环、Escape 关闭和焦点返回。笔记继续只保存在本机。
- 行动区或视口变化时自动保持最新发言可见；玩家向上回看历史时保留阅读位置，并提示新消息。
- 切换大厅、对局和结算时回到页面顶部。历史对局为可键盘访问的链接，复盘可直接刷新打开。

## 实际页面与验证

开发期界面截图含有本地账号昵称，未放入公开仓库。匿名化示例见 [登录](screenshots/login.jpg)、[对局](screenshots/gameplay.jpg)、[复盘](screenshots/review.jpg)。

浏览器实测桌面 1440×900、1280×800 与手机 390×844。十二座位、身份卡、发言提交、狼人目标确认、竞选、警长投票、放逐目标选择、推理簿、消息跟随与历史复盘均已检查；测试视口未出现横向溢出。`node --check static/app.js` 和 `git diff --check` 通过，现有 Python 测试 79 项通过。

截图和交互使用独立临时数据库与模拟 AI 预览服务，不消耗真实模型调用，不代表真实 LLM 的发言质量。

## 生成提示词

### village

Use case: stylized-concept. Asset type: production background art for a Chinese Werewolf social deduction game, not a UI mockup. Create an exquisite wide 16:9 landscape hand-painted illustration: a mysterious old European mountain village at blue hour under a huge luminous ivory full moon, steep tiled rooftops, a crooked bell tower, winding cobblestone lanes, pine silhouettes, a few warm amber windows, fine drifting fog. Stylish premium illustrated board-game art, confident painterly shapes, intricate but tasteful details, atmospheric depth, NOT photorealistic, NOT childish clipart. Palette midnight indigo, desaturated slate blue, muted plum shadows, pale moonlight, honey amber accents. Composition: village clustered mostly in middle and right, quiet dark mist and pines on the left third for readable overlay text; village scene remains beautifully legible without being nearly black. Foreground sparse branches frame edges. Lighting creates a magical suspenseful invitation, not gore or horror. No characters, no wolves, no text, no letters, no numbers, no UI, no logos, no watermark. Deliver a coherent polished game illustration.

### portraits

Use case: stylized-concept. Asset type: one production character portrait atlas for a Werewolf social deduction game. EXACT layout: 4 equal columns by 3 equal rows, 12 square portraits edge-to-edge filling one 4:3 landscape canvas. Each cell is an individual unique HUMAN townspeople bust portrait centered with the full face and shoulders in view. Every portrait occupies precisely one equal rectangular cell with consistent framing; NO gutter, NO frames, NO borders, NO labels. Same exquisite premium hand-painted fantasy board-game illustration style throughout, appealing expressive faces readable at 64 pixels, brushwork and subtle texture. 12 clearly different identities in row-major order: 1 young auburn-haired woman with a confident half-smile; 2 dark-haired man with round glasses and a curious expression; 3 middle-aged bearded man with a calm smile; 4 young woman with short black bob and sharp attentive gaze; 5 elderly silver-haired woman with a knowing smile; 6 tan young man with curly brown hair and an amused expression; 7 blonde woman with braided hair and a thoughtful expression; 8 older grey-haired man with a moustache and skeptical expression; 9 cheerful dark-skinned woman with dark curls; 10 young pale man with straight black hair and a quiet expression; 11 red-haired freckled man with a mischievous smile; 12 woman with long dark hair and a steady serious expression. All wear simple rich-colored medieval village clothing, no crowns, weapons, crystal balls, witch hats, wolf masks or other role clues. These are avatars, not hidden roles. Background of EACH portrait is a subdued textured dark-indigo or dusty-plum backdrop. Palette harmonized midnight indigo, slate blue, plum, cream skin highlights, warm amber. Expressive human character, premium digital painted illustration, sophisticated and cinematic, not anime, not photographic, not 3D. No text or watermark. Strict 4 columns, 3 rows, equal cells, 12 portraits.

### roles

Use case: stylized-concept. Asset type: one production Werewolf role-card illustration atlas. EXACT layout: 3 equal columns by 2 equal rows, six square painted illustrations edge-to-edge filling one 3:2 landscape canvas. Equal precise cells, no gutters, no frames, no labels, no text. Same premium hand-painted dark fantasy board-game style, elegant painterly shapes, fine detail, dramatic rim light, sophisticated and appealing rather than frightening. Row-major subjects: top-left WEREWOLF: powerful charcoal-furred wolf face, glowing amber eyes, moonlight behind, no blood; top-middle VILLAGER: kind rustic villager holding a lantern, linen and wool clothing, amber warm light; top-right SEER: wise silver-haired mystic with small luminous blue crystal orb and starry navy cloak; bottom-left WITCH: intriguing adult woman with a plum hood, two small glass potion bottles cyan and amber, no cartoon pointy hat; bottom-middle HUNTER: rugged woodsman in leather, antique crossbow across chest, golden side light; bottom-right FOOL: whimsical curly-haired young villager with a playful half-smile and a tiny paper flower, thoughtful rather than grotesque. Each subject centered as a chest-up iconic character portrait with generous headroom so it can be cropped into a narrow game identity card. Midnight indigo, dusty plum, moonlit silver, antique ivory and amber, slate shadows. Consistent scale and quality in all six cells. This art will only be used for the player's own role or after roles are revealed. No logos, no writing, no symbols that look like letters, no watermark. Strict 3 columns, 2 rows.

const main = document.querySelector("main");
const params = new URLSearchParams(location.search);
const view = ["archive", "search"].includes(params.get("view")) ? params.get("view") : document.body.dataset.page;
const pages = { news: "./news.html", papers: "./index.html" };
const requests = new Map();
const labels = {
  zh: {
    navNews: "资讯", navPapers: "论文", navArchive: "存档", navSearch: "搜索", brand: "机器人研究日报",
    papers: "机器人论文精选", paperIssue: "本期机器人论文", news: "机器人资讯日报", newsPicks: "今日资讯精选", archive: "日报存档", search: "全文搜索",
    arxiv: "arXiv cs.RO", rules: "规则筛选 · 保留来源原文", api: "双语编选", selectedPapers: "从 {n} 篇论文中筛选",
    selectedNews: "从 {n} 条资讯中筛选", noPapers: "暂无论文期次。", noNews: "暂无资讯期次。", noItems: "本期暂无精选内容。",
    loadMore: "加载较早期次", viewIssue: "查看本期 {n} 篇论文 →", older: "较早一期 {date} →", newer: "← 较新一期 {date}",
    allArchive: "全部存档 →", industry: "行业资讯", focus: "今日焦点", top3: "当日 Top 3", download: "下载 PNG ↓",
    quick: "快讯", rss: "RSS 订阅 ↗", noDay: "这一天暂无内容。", archiveLink: "查看存档 →", newsCount: "条", paperCount: "篇",
    searchPlaceholder: "输入标题、摘要或主题关键词", searchButton: "搜索", searching: "正在搜索…", noResults: "没有找到匹配内容。",
    found: "找到 {n} 条结果", readOriginal: "阅读原文 ↗", paperSections: ["背景与痛点", "结论与结果", "方法提纯", "研究启发"],
    arxivLink: "arXiv 原文 ↗", pdfLink: "PDF ↗", githubLink: "GitHub ↗", projectLink: "Project ↗", figureAlt: "论文配图",
    sourceDate: "{source} · 发布于 {date}", unreadable: "无法读取日报数据。请通过本地预览服务或 GitHub Pages 打开。",
    navigation: "主导航", language: "语言切换", skip: "跳到正文", loading: "正在读取日报…",
    failed: "（{n} 份期次文件读取失败）", issueFailed: "{date} 的论文读取失败。",
    reading: "阅读操作", favorite: "收藏", favorited: "取消收藏", read: "标为已读", unread: "标为未读",
    onlyFavorites: "仅看收藏（{n}）", noFavorites: "这里还没有收藏。", copy: "复制 Markdown",
    copied: "已复制", copyFailed: "复制失败", original: "原文",
    featured: "今日资讯精选", selectedNewsWindow: "从近 {hours} 小时内的 {n} 条资讯中筛选",
    paperSourceDate: "精选自 {date} arXiv {category}", imageCredit: "图片来源",
  },
  en: {
    navNews: "News", navPapers: "Papers", navArchive: "Archive", navSearch: "Search", brand: "Robo Daily Digest",
    papers: "Robotics Paper Picks", paperIssue: "Robotics Papers", news: "Robotics News", newsPicks: "TOP ROBOTICS STORIES", archive: "Digest Archive", search: "Full-text Search",
    arxiv: "arXiv cs.RO", rules: "Rule picks · source text retained", api: "Bilingual edition", selectedPapers: "Selected from {n} papers",
    selectedNews: "Selected from {n} stories", noPapers: "No paper editions yet.", noNews: "No news editions yet.", noItems: "No selected items in this edition.",
    loadMore: "Load earlier editions", viewIssue: "View all {n} papers →", older: "Earlier edition {date} →", newer: "← Newer edition {date}",
    allArchive: "Full archive →", industry: "Industry news", focus: "Today’s focus", top3: "Today’s Top 3", download: "Download PNG ↓",
    quick: "Briefs", rss: "RSS ↗", noDay: "No content for this date.", archiveLink: "Browse archive →", newsCount: "stories", paperCount: "papers",
    searchPlaceholder: "Search titles, summaries, or topics", searchButton: "Search", searching: "Searching…", noResults: "No matching content.",
    found: "{n} results", readOriginal: "Read source ↗", paperSections: ["Background", "Conclusion & results", "Method", "Research takeaways"],
    arxivLink: "arXiv ↗", pdfLink: "PDF ↗", githubLink: "GitHub ↗", projectLink: "Project ↗", figureAlt: "Paper figure",
    sourceDate: "{source} · published {date}", unreadable: "The digest data could not be read. Open it through a local preview server or GitHub Pages.",
    navigation: "Main navigation", language: "Language", skip: "Skip to content", loading: "Loading digest…",
    failed: " ({n} edition files failed to load)", issueFailed: "Papers for {date} could not be loaded.",
    reading: "Reading actions", favorite: "Save", favorited: "Remove from favorites", read: "Mark as read",
    unread: "Mark as unread", onlyFavorites: "Favorites only ({n})", noFavorites: "No favorites here yet.",
    copy: "Copy Markdown", copied: "Copied", copyFailed: "Could not copy", original: "Source",
    featured: "Featured Robotics Stories", selectedNewsWindow: "Selected from {n} stories in the past {hours} hours",
    paperSourceDate: "From arXiv {category} submissions on {date} (UTC)", imageCredit: "Image source",
  },
};
const languageKey = "robo-digest-lang";
const readingKey = "robo-digest-reading-v1";
const sectionKeys = ["background", "conclusion", "method", "takeaways"];
let englishSources = {};
let favoritesOnly = false;
const readingState = new Map();
try {
  const saved = JSON.parse(localStorage.getItem(readingKey) || "{}");
  for (const [key, flags] of Object.entries(saved || {})) {
    if (Number.isInteger(flags) && flags >= 1 && flags <= 3) readingState.set(key, flags);
  }
} catch { /* reading state still works for this visit */ }
function initialLanguage() {
  const explicit = params.get("lang");
  if (explicit === "zh" || explicit === "en") return explicit;
  try {
    if (localStorage.getItem(languageKey) === "en") return "en";
  } catch { /* storage can be unavailable in private contexts */ }
  return "zh";
}
let currentLang = initialLanguage();
let editions;
let renderVersion = 0;
let paperPageSize = 12;
let searchInput = params.get("q") || "";
const revealObserver = typeof IntersectionObserver === "function" ? new IntersectionObserver(entries => {
  for (const entry of entries) {
    if (!entry.isIntersecting) continue;
    showRevealed(entry.target);
  }
}, { threshold: 0.06 }) : null;

function t(key, values = {}) {
  let value = labels[currentLang][key] ?? labels.zh[key] ?? key;
  for (const [name, replacement] of Object.entries(values)) value = value.replace(`{${name}}`, replacement);
  return value;
}

function el(tag, attributes = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attributes)) if (value !== undefined && value !== null) node.setAttribute(key, value);
  node.append(...children.filter(child => child !== null && child !== undefined));
  return node;
}

function reveal(node) {
  if (revealObserver) {
    node.classList.add("will-reveal");
    revealObserver.observe(node);
  }
  return node;
}

function showRevealed(node, immediate = false) {
  node.classList.replace("will-reveal", "did-reveal");
  if (immediate) node.classList.add("reveal-immediate");
  revealObserver?.unobserve(node);
}

function localized(item) {
  const locale = item?.i18n?.[currentLang] || item?.i18n?.zh || item || {};
  return { ...item, ...locale };
}

function sourceName(item) {
  return currentLang === "en" && Object.hasOwn(englishSources, item.source) ? englishSources[item.source] : item.source;
}

function itemKey(kind, item) { return `${kind}/${item.id}`; }
function isFavorite(key) { return Boolean(readingState.get(key) & 1); }
function isRead(key) { return Boolean(readingState.get(key) & 2); }

function setReading(key, favorite, read) {
  if (favorite || read) readingState.set(key, Number(favorite) + 2 * Number(read));
  else readingState.delete(key);
  try { localStorage.setItem(readingKey, JSON.stringify(Object.fromEntries(readingState))); } catch { /* in-memory state remains */ }
  syncReadingView();
}

function syncReadingView() {
  const toggle = main.querySelector(".favorites-filter");
  const saved = new Set();
  for (const item of main.querySelectorAll("[data-reading-item]")) {
    const key = item.dataset.readingItem;
    const favorite = isFavorite(key);
    const read = isRead(key);
    if (favorite) saved.add(key);
    item.hidden = toggle && favoritesOnly ? !favorite : item.hasAttribute("data-extra-paper");
    item.classList.toggle("is-read", read);
    const star = item.querySelector(".favorite-button");
    const mark = item.querySelector(".read-button");
    star.setAttribute("aria-pressed", String(favorite));
    star.title = t(favorite ? "favorited" : "favorite");
    star.setAttribute("aria-label", star.title);
    star.textContent = favorite ? "★" : "☆";
    mark.setAttribute("aria-pressed", String(read));
    mark.textContent = t(read ? "unread" : "read");
  }
  if (!toggle) return;
  toggle.setAttribute("aria-pressed", String(favoritesOnly));
  toggle.textContent = t("onlyFavorites", { n: saved.size });
  main.querySelector(".favorites-empty").hidden = !favoritesOnly || saved.size > 0;
  for (const group of main.querySelectorAll("[data-favorites-group]")) {
    group.hidden = favoritesOnly && !group.querySelector("[data-reading-item]:not([hidden])");
  }
  for (const node of main.querySelectorAll("[data-favorites-hide]")) node.hidden = favoritesOnly;
}

function favoritesControls() {
  const button = el("button", { type: "button", class: "favorites-filter" });
  button.addEventListener("click", () => {
    favoritesOnly = !favoritesOnly;
    syncReadingView();
  });
  return button;
}

function favoriteStatus() {
  return el("p", { class: "status favorites-empty", role: "status", hidden: "" }, t("noFavorites"));
}

function itemMarkdown(item, kind) {
  const parts = [`# ${item.title.replace(/[\r\n]+/g, " ")}`, item.summary];
  if (item.sections) {
    sectionKeys.forEach((key, index) => {
      if (item.sections[key]) parts.push(`## ${t("paperSections")[index]}\n\n${item.sections[key]}`);
    });
  } else {
    parts.push(...(item.analysis || []).filter(line => line !== item.summary));
  }
  const links = [[item.url, t("original")]];
  if (kind === "papers") links.push([item.code_url, "GitHub"], [item.project_url, "Project"]);
  for (const [url, label] of links) {
    if (url && /^https?:\/\//i.test(url)) parts.push(`- [${label}](<${url.replaceAll(">", "%3E")}>)`);
  }
  return parts.filter(Boolean).join("\n\n");
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const field = el("textarea", { class: "clipboard-field", readonly: "" });
    const active = document.activeElement;
    document.body.append(field);
    field.value = text;
    field.select();
    field.setSelectionRange(0, text.length);
    try {
      return document.execCommand("copy");
    } catch {
      return false;
    } finally {
      field.remove();
      active?.focus({ preventScroll: true });
    }
  }
}

function readingActions(item, kind) {
  const key = itemKey(kind, item);
  const star = el("button", { type: "button", class: "favorite-button", "aria-label": t("favorite") });
  star.addEventListener("click", () => setReading(key, !isFavorite(key), isRead(key)));
  const mark = el("button", { type: "button", class: "read-button" });
  mark.addEventListener("click", () => setReading(key, isFavorite(key), !isRead(key)));
  const copy = el("button", { type: "button", class: "copy-button", "aria-live": "polite" }, t("copy"));
  let timer;
  copy.addEventListener("click", async () => {
    clearTimeout(timer);
    copy.textContent = t(await copyText(itemMarkdown(item, kind)) ? "copied" : "copyFailed");
    timer = setTimeout(() => { copy.textContent = t("copy"); }, 1600);
  });
  return el("div", { class: "reading-actions", role: "group", "aria-label": t("reading") }, star, mark, copy);
}

function requestJSON(path) {
  if (!requests.has(path)) requests.set(path, fetch(path).then(response => {
    if (!response.ok) throw new Error(`HTTP ${response.status} for ${path}`);
    return response.json();
  }).catch(error => { requests.delete(path); throw error; }));
  return requests.get(path);
}

function dateLabel(date) { return el("time", { class: "date-label", datetime: date }, date); }
function heading(title, ...extras) { return el("header", { class: "page-heading" }, el("h1", {}, title), ...extras); }
function status(message) { return el("p", { class: "status", role: "status" }, message); }
function ordinal(index) { return el("span", { class: "ordinal", "aria-hidden": "true" }, String(index + 1).padStart(2, "0")); }
function itemId(item) { return `item-${item.id}`; }
function issueLink(kind, day, item) { return `${pages[kind]}?date=${encodeURIComponent(day)}&lang=${currentLang}${item ? `#${encodeURIComponent(itemId(item))}` : ""}`; }
function issueNote(issue) { return issue.mode === "rules" ? t("rules") : t("api"); }

function sourceLink(item, label, readKey) {
  try {
    const url = new URL(item.url);
    if (["http:", "https:"].includes(url.protocol)) {
      const link = el("a", { href: url.href, target: "_blank", rel: "noopener noreferrer" }, label);
      if (readKey) {
        const mark = () => setReading(readKey, isFavorite(readKey), true);
        link.addEventListener("click", mark);
        link.addEventListener("auxclick", event => { if (event.button === 1) mark(); });
      }
      return link;
    }
  } catch { /* keep malformed sources as plain text */ }
  return el("span", {}, label);
}

function tagList(item) {
  return el("ul", { class: "tags", "aria-label": currentLang === "zh" ? "主题标签" : "Topic tags" }, ...(item.tags || []).map(tag => el("li", {}, tag)));
}

function sourceBox(item) {
  const date = item.published ? item.published.slice(0, 10) : "";
  return el("p", { class: "source" }, sourceLink(item, `${sourceName(item)} · ${t("readOriginal")}`, itemKey("news", item)),
    date ? el("time", { datetime: item.published }, date) : null);
}

function paperActions(item) {
  const actions = [
    [item.url, t("arxivLink")], [item.pdf_url || `https://arxiv.org/pdf/${item.id}`, t("pdfLink")],
    [item.code_url, t("githubLink")], [item.project_url, t("projectLink")],
  ].filter(([url]) => url);
  return el("div", { class: "paper-actions" }, ...actions.map(([url, label]) =>
    sourceLink({ url }, label, itemKey("papers", item))));
}

function paperFigure(item) {
  if (!item.figure?.path) return null;
  const figure = el("figure", { class: "paper-figure" },
    el("a", { href: item.figure.path, target: "_blank", rel: "noopener noreferrer" },
      el("img", { src: item.figure.path, loading: "lazy", alt: item.figure.caption || t("figureAlt") })),
    item.figure.caption ? el("figcaption", {}, item.figure.caption) : "");
  return figure;
}

function paperSections(item) {
  if (!item.sections) {
    return el("div", {}, ...(item.analysis?.length ? item.analysis : [item.summary]).map(line => el("p", {}, line)));
  }
  const content = el("div", { class: "paper-sections" });
  for (const [index, key] of sectionKeys.entries()) {
    if (item.sections[key]) content.append(el("section", {}, el("h3", {}, labels[currentLang].paperSections[index]), el("p", {}, item.sections[key])));
  }
  return content;
}

function paperAttention(item) {
  const metrics = item.attention || {};
  const tags = [];
  if (metrics.hf_upvotes > 0) tags.push(`HF ↑${metrics.hf_upvotes}`);
  if (metrics.hf_comments > 0) tags.push(`HF ◇ ${metrics.hf_comments}`);
  if (metrics.github_stars > 0) tags.push(`★ ${metrics.github_stars.toLocaleString()}`);
  return tags.length ? el("span", { class: "paper-attention" }, tags.join(" · ")) : null;
}

function paperRow(rawItem, index, standalone, day) {
  const item = localized(rawItem);
  const key = itemKey("papers", item);
  const body = el("div", { class: "paper-body" }, paperSections(item), paperFigure(item), tagList(item), paperActions(item), readingActions(item, "papers"));
  const summary = el("summary", {}, ordinal(index), el("span", {},
    el("span", { class: "paper-title" }, item.title), paperAttention(item)),
    el("span", { class: "row-toggle", "aria-hidden": "true" }));
  const row = el("details", { class: `paper-row${index === 0 ? " headline" : ""}`,
    "data-paper": `${day}/${itemId(rawItem)}`, "data-reading-item": key,
    "data-extra-paper": !standalone && index >= 3 ? "" : null }, summary, body);
  summary.addEventListener("click", () => { if (!row.open) setReading(key, isFavorite(key), true); });
  if (standalone) row.id = itemId(rawItem);
  return row;
}

function paperCard(issue, standalone) {
  const card = el("article", { class: "paper-card", "data-favorites-group": "" });
  const count = t("selectedPapers", { n: issue.total_scanned });
  const sourceDate = issue.paper_source_date ?
    el("span", { class: "meta paper-source-date" }, t("paperSourceDate", { date: issue.paper_source_date, category: issue.paper_category || "cs.RO" })) :
    null;
  card.append(el("header", { class: "paper-top" }, el("div", {}, dateLabel(issue.date), sourceDate), standalone ?
    el("span", { class: "meta" }, count) : el("a", { class: "quiet-link", href: issueLink("papers", issue.date) }, `${count} ↗`)));
  const items = issue.items;
  card.append(...items.map((item, index) => paperRow(item, index, standalone, issue.date)));
  if (!items.length) card.append(status(t("noItems")));
  if (!standalone && issue.items.length) card.append(el("a", { class: "paper-more", "data-favorites-hide": "", href: issueLink("papers", issue.date) }, t("viewIssue", { n: issue.items.length })));
  return card;
}

function pager(kind, issues, current) {
  const index = issues.indexOf(current), newer = issues[index - 1], older = issues[index + 1];
  return el("div", { class: "pager" }, newer ? el("a", { class: "quiet-link", href: issueLink(kind, newer.date) }, t("newer", { date: newer.date })) : el("span"),
    older ? el("a", { class: "quiet-link", href: issueLink(kind, older.date) }, t("older", { date: older.date })) :
      el("a", { class: "quiet-link", href: `./index.html?view=archive&lang=${currentLang}` }, t("allArchive")));
}

function makePaperPage(version) {
  const issues = editions.filter(entry => entry.papers);
  if (params.get("date")) {
    const entry = issues.find(item => item.date === params.get("date"));
    if (!entry) return showMissing(t("noDay"));
    return requestJSON(entry.papers.path).then(issue => {
      if (version !== renderVersion) return;
      main.replaceChildren(heading(t("paperIssue"), el("div", { class: "reading-meta" },
        el("p", { class: "meta" }, issueNote(issue)), favoritesControls())),
        favoriteStatus(), paperCard(issue, true), pager("papers", issues, entry));
    });
  }
  const list = el("section", { class: "stack", "aria-label": t("papers") });
  const more = el("button", { type: "button", class: "action load-more" }, t("loadMore"));
  main.replaceChildren(heading(t("papers"), el("div", { class: "reading-meta" },
    el("p", { class: "meta" }, t("arxiv")), favoritesControls())), favoriteStatus(), list, more);
  if (!issues.length) list.append(status(t("noPapers")));
  let offset = 0;
  async function appendPage(size) {
    more.disabled = true;
    const page = issues.slice(offset, offset + size);
    const results = await Promise.allSettled(page.map(entry => requestJSON(entry.papers.path)));
    if (version !== renderVersion) return;
    results.forEach((result, index) => list.append(result.status === "fulfilled" ? reveal(paperCard(result.value, false)) :
      status(t("issueFailed", { date: page[index].date }))));
    offset += page.length; more.hidden = offset >= issues.length; more.disabled = false;
    syncReadingView();
  }
  more.addEventListener("click", () => {
    paperPageSize += 12;
    appendPage(12).catch(showError);
  });
  return appendPage(paperPageSize);
}

function newsFigure(item) {
  if (!item.image?.path) return null;
  const image = el("img", {
    src: item.image.path, loading: "lazy", width: item.image.width, height: item.image.height,
    alt: item.image.alt || item.title,
  });
  const figure = el("figure", { class: "paper-figure news-figure" }, image,
    el("figcaption", {}, sourceLink({ url: item.image.source_url }, `${t("imageCredit")} · ${sourceName(item)}`)));
  image.addEventListener("error", () => { figure.hidden = true; });
  return figure;
}

function newsCard(rawItem, index) {
  const item = localized(rawItem);
  return el("article", { id: itemId(rawItem), class: "lead-card", "data-reading-item": itemKey("news", item) },
    el("header", { class: "lead-heading" }, ordinal(index), el("h2", {}, item.title)),
    el("div", { class: "lead-content" }, newsFigure(item), ...(item.analysis || [item.summary]).map(line => el("p", {}, line)),
      tagList(item), sourceBox(item), readingActions(item, "news")));
}

function quickNews(rawItems) {
  const cards = rawItems.map((rawItem, index) => {
    const item = localized(rawItem);
    const key = itemKey("news", item);
    return reveal(el("li", { id: itemId(rawItem), class: "quick-item", "data-reading-item": key }, ordinal(index + 3), el("div", {},
      el("h3", {}, sourceLink(rawItem, item.title, key)), el("p", {}, item.summary),
      el("p", { class: "quick-meta" }, rawItem.published ?
        t("sourceDate", { source: sourceName(rawItem), date: rawItem.published.slice(0, 10) }) : sourceName(rawItem)),
      readingActions(item, "news"))));
  });
  return el("section", { class: "quick-news", "aria-labelledby": "quick-heading", "data-favorites-group": "" },
    el("header", { class: "section-top" }, el("h2", { id: "quick-heading" }, t("quick"))),
    el("ol", { class: "quick-list", start: 4 }, ...cards));
}

function makeNewsPage(version) {
  const issues = editions.filter(entry => entry.news), entry = params.get("date") ? issues.find(item => item.date === params.get("date")) : issues[0];
  if (!entry) return showMissing(params.get("date") ? t("noDay") : t("noNews"));
  return requestJSON(entry.news.path).then(issue => {
    if (version !== renderVersion) return;
    const scan = Number.isInteger(issue.window_count) ?
      t("selectedNewsWindow", { n: issue.window_count, hours: issue.window_hours || 48 }) :
      t("selectedNews", { n: issue.total_scanned });
    const top = issue.items.slice(0, 3), header = el("header", { class: "page-heading" }, el("p", { class: "kicker" }, dateLabel(entry.date), el("span", { class: "divider", "aria-hidden": "true" }), t("industry")),
      el("h1", {}, top.length === 3 ? t("news") : t("newsPicks")), el("ul", { class: "lede", "aria-label": t("focus"), "data-favorites-hide": "" }, ...top.map(raw => { const item = localized(raw); return el("li", {}, el("a", { href: `#${encodeURIComponent(itemId(raw))}` }, item.lede || item.title)); })),
      el("div", { class: "reading-meta issue-note" },
        el("p", { class: "meta" }, `${issueNote(issue)} · ${scan}`), favoritesControls()));
    const leads = el("section", { class: "stack", "aria-label": t("focus"), "data-favorites-group": "" },
      ...top.map((item, index) => reveal(newsCard(item, index))));
    main.replaceChildren(header, favoriteStatus(), top.length ? leads : status(t("noItems")));
    const infographic = currentLang === "en" ? entry.infographic_en || entry.infographic : entry.infographic;
    if (infographic && top.length) {
      const image = el("img", { src: infographic, width: 1440, height: 650, loading: "lazy", alt: top.map(item => localized(item).title).join(" · ") });
      const download = el("a", { href: infographic, class: "quiet-link",
        download: `robo-top-3-${entry.date}-${currentLang}.png` }, t("download"));
      const imageHeader = el("header", { class: "section-top" }, el("h2", {}, t(top.length === 3 ? "top3" : "featured")), download);
      main.append(reveal(el("figure", { class: "infographic", "data-favorites-hide": "" }, imageHeader, image)));
    }
    if (issue.items.length > 3) main.append(quickNews(issue.items.slice(3)));
    main.append(pager("news", issues, entry));
  });
}

function renderArchive() {
  const rows = editions.map(entry => el("article", { class: "archive-row" }, el("h2", {}, dateLabel(entry.date)), el("div", {}, ...["news", "papers"].filter(kind => entry[kind]).map(kind => el("a", { href: issueLink(kind, entry.date), class: "quiet-link" }, `${labels[currentLang][kind === "news" ? "navNews" : "navPapers"]} · ${entry[kind].count} ${t(kind === "news" ? "newsCount" : "paperCount")} →`)))));
  main.replaceChildren(heading(t("archive")), ...(rows.length ? rows : [status(t("noDay"))]));
}

function normalized(text) { return String(text || "").normalize("NFKC").toLocaleLowerCase(); }

async function searchEditions(query, note, results, version) {
  const inputs = editions.flatMap(entry => ["news", "papers"].filter(kind => entry[kind]).map(kind => ({ path: entry[kind].path, date: entry.date, kind })));
  let count = 0, failed = 0;
  for (let start = 0; start < inputs.length; start += 8) {
    const batch = inputs.slice(start, start + 8), loaded = await Promise.allSettled(batch.map(input => requestJSON(input.path)));
    if (version !== renderVersion) return;
    for (const [index, result] of loaded.entries()) {
      if (result.status !== "fulfilled") { failed++; continue; }
      const { kind, date } = batch[index];
      for (const raw of result.value.items) {
        const item = localized(raw);
        const fields = [raw, ...Object.values(raw.i18n || {})].flatMap(locale => [
          locale.title, locale.summary, locale.lede, ...(locale.tags || []),
          ...(locale.analysis || []), ...Object.values(locale.sections || {}),
        ]);
        const text = normalized([raw.original_title, raw.original_summary, raw.source, ...(raw.authors || []), ...fields].filter(Boolean).join(" "));
        if (!query.every(word => text.includes(word))) continue;
        count++; results.append(el("article", { class: "search-result", "data-reading-item": itemKey(kind, raw) },
          el("p", { class: "meta" }, `${date} · ${kind === "news" ? labels[currentLang].navNews : labels[currentLang].navPapers} · ${sourceName(raw)}`),
          el("h2", {}, el("a", { href: issueLink(kind, date, raw) }, item.title)), el("p", {}, item.summary),
          readingActions(item, kind)));
      }
    }
  }
  note.textContent = count ? t("found", { n: count }) : t("noResults");
  if (failed) note.textContent += t("failed", { n: failed });
}

function renderSearch(version) {
  const query = params.get("q")?.trim() || "";
  const input = el("input", { type: "search", name: "q", value: searchInput,
    "aria-label": t("search"), placeholder: t("searchPlaceholder"), required: "" });
  input.addEventListener("input", () => { searchInput = input.value; });
  const form = el("form", { class: "search-form", action: "./index.html", role: "search" },
    el("input", { type: "hidden", name: "view", value: "search" }),
    el("input", { type: "hidden", name: "lang", value: currentLang }), input,
    el("button", { type: "submit", class: "action" }, t("searchButton")));
  const note = status(query ? t("searching") : "");
  const results = el("section", { "aria-label": t("search") });
  main.replaceChildren(heading(t("search")), form, note, results);
  if (query) return searchEditions(normalized(query).split(/\s+/), note, results, version);
}

function scrollToItem() {
  if (!location.hash || location.hash === "#content") return;
  let target; try { target = document.getElementById(decodeURIComponent(location.hash.slice(1))); } catch { return; }
  if (!target || target.hidden) return;
  for (const node of main.querySelectorAll(".will-reveal, .did-reveal")) {
    if (node === target || node.contains(target) || node.compareDocumentPosition(target) & Node.DOCUMENT_POSITION_FOLLOWING) {
      showRevealed(node, true);
    }
  }
  if (target.tagName === "DETAILS") target.open = true;
  target.scrollIntoView({ block: "start" });
}

function syncChrome() {
  document.documentElement.lang = currentLang === "zh" ? "zh-CN" : "en";
  document.querySelector(".brand").lastChild.textContent = t("brand");
  document.querySelector(".skip").textContent = t("skip");
  document.querySelector("nav").setAttribute("aria-label", t("navigation"));
  document.querySelector(".language-switch").setAttribute("aria-label", t("language"));
  document.querySelector(".site-footer span").textContent = t("brand");
  document.querySelector(".site-footer a").textContent = t("rss");
  const home = document.querySelector(".brand");
  home.href = `./news.html?lang=${currentLang}`;
  document.querySelectorAll("nav a[data-view]").forEach(link => {
    const key = { news: "navNews", papers: "navPapers", archive: "navArchive", search: "navSearch" }[link.dataset.view];
    link.childNodes.length > 1 ? link.lastChild.textContent = t(key) : link.textContent = t(key);
    link.href = pages[link.dataset.view] ? `${pages[link.dataset.view]}?lang=${currentLang}` :
      `./index.html?view=${link.dataset.view}&lang=${currentLang}`;
  });
  document.querySelectorAll("[data-lang]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.lang === currentLang)));
  document.title = `${labels[currentLang][view] || t("brand")} · ${t("brand")}`;
}

function showMissing(message) {
  main.replaceChildren(heading(t(view)), status(message),
    el("a", { class: "quiet-link", href: `./index.html?view=archive&lang=${currentLang}` }, t("archiveLink")));
}

function showError(error) {
  console.error(error);
  main.replaceChildren(heading(t(view)), status(t("unreadable")));
}

async function render(restore) {
  syncChrome();
  if (!editions) return;
  const version = ++renderVersion;
  revealObserver?.disconnect();
  const renderers = { papers: makePaperPage, news: makeNewsPage, archive: renderArchive, search: renderSearch };
  try {
    await renderers[view](version);
    if (version !== renderVersion) return;
    syncReadingView();
    if (restore) {
      main.querySelectorAll("details[data-paper]").forEach(row => { row.open = restore.open.has(row.dataset.paper); });
      if (location.hash && location.hash !== "#content") scrollToItem();
      else window.scrollTo({ top: restore.top, behavior: "instant" });
    } else {
      scrollToItem();
    }
  } catch (error) {
    if (version === renderVersion) showError(error);
  }
}

document.querySelector(`nav a[data-view="${view}"]`)?.setAttribute("aria-current", "page");
document.querySelectorAll("[data-lang]").forEach(button => button.addEventListener("click", () => {
  if (currentLang === button.dataset.lang) return;
  const restore = {
    top: window.scrollY,
    open: new Set(Array.from(main.querySelectorAll("details[open]"), row => row.dataset.paper)),
  };
  currentLang = button.dataset.lang;
  try { localStorage.setItem(languageKey, currentLang); } catch { /* the URL still carries the language */ }
  params.set("lang", currentLang);
  history.replaceState(null, "", `${location.pathname}?${params}${location.hash}`);
  render(restore);
}));
document.addEventListener("click", event => {
  if (event.target.closest(".lede a[href^='#']")) requestAnimationFrame(scrollToItem);
});
window.addEventListener("hashchange", scrollToItem);
syncChrome();
main.replaceChildren(status(t("loading")));
Promise.all([
  requestJSON("./data/index.json"),
  requestJSON("./assets/source-names.json").catch(() => ({})),
]).then(([data, sources]) => {
  englishSources = sources;
  editions = data.editions || [];
  render();
}).catch(showError);

-- Zhouyi: pandoc filter turning a PDF-mode Chinese Markdown into LaTeX (used by zhouyi.py md2pdf)
-- 1. A block quote that starts with a bold 译注 becomes yizhubox; other quotes become quotebox
-- 2. An image paragraph and the caption paragraph after it become a float: figure captions below,
--    table captions above; the width follows the 300 DPI crop
-- 3. <sup>N</sup> becomes a superscript; a "**N** text" paragraph becomes a small footnote paragraph
-- 4. Headings: bookmarks use the Chinese part; the English in brackets is shown muted
-- 5. Inline code and URLs may break; a paragraph of one bold run (a run-in heading) is kept off the page foot

local TEXTWIDTH_PT = 448  -- the text width set in template.tex
local DPI = 300

local function latex(inlines)
  return pandoc.write(pandoc.Pandoc({pandoc.Plain(inlines)}), "latex"):gsub("\n", " ")
end

local function png_width_pt(path)
  local f = io.open(path, "rb")
  if not f then return nil end
  local h = f:read(24)
  f:close()
  if not h or #h < 24 or h:sub(2, 4) ~= "PNG" then return nil end
  local w = (h:byte(17) << 24) | (h:byte(18) << 16) | (h:byte(19) << 8) | h:byte(20)
  return w / DPI * 72
end

-- Inline: <sup> to superscript, bare URLs to \url
local function fix_inlines(inls)
  local out = pandoc.List()
  local i = 1
  while i <= #inls do
    local el = inls[i]
    if el.t == "RawInline" and el.format == "html" and el.text == "<sup>" then
      local j, inner = i + 1, pandoc.List()
      while j <= #inls and not (inls[j].t == "RawInline" and inls[j].text == "</sup>") do
        inner:insert(inls[j]); j = j + 1
      end
      out:insert(pandoc.Superscript(inner))
      i = j + 1
    elseif el.t == "Str" and el.text:match("^https?://") then
      local url, tail = el.text:match("^(.-)([%.,;:%)。，；：）]*)$")
      out:insert(pandoc.RawInline("latex", "\\url{" .. url .. "}"))
      if tail ~= "" then out:insert(pandoc.Str(tail)) end
      i = i + 1
    else
      out:insert(el)
      i = i + 1
    end
  end
  return out
end

function Inlines(inls)
  return fix_inlines(inls)
end

-- Inline code: escape by hand and allow breaks after / _ . -
function Code(el)
  local s = el.text
  local map = {
    ["\\"] = "\\textbackslash{}", ["{"] = "\\{", ["}"] = "\\}", ["$"] = "\\$", ["&"] = "\\&",
    ["#"] = "\\#", ["^"] = "\\textasciicircum{}", ["_"] = "\\_", ["%"] = "\\%", ["~"] = "\\textasciitilde{}",
  }
  s = s:gsub("[\\{}%$&#%^_%%~]", map)
  s = s:gsub("([/%.%-])", "%1\\allowbreak{}"):gsub("(\\_)", "%1\\allowbreak{}")
  if el.text:find("[\228-\233]") then
    return pandoc.RawInline("latex", "{\\xeCJKsetup{CJKecglue={}}\\texttt{" .. s .. "}}")
  end
  return pandoc.RawInline("latex", "\\texttt{" .. s .. "}")
end

-- Headings
local function split_title(text)
  local zh, en = text:match("^(.-)（(.+)）$")
  if zh and zh ~= "" then return zh, en end
  return text, nil
end

function Header(el)
  -- The level-1 heading is the paper title, set at the top of page 1 and used in the header;
  -- level 2 and below map to section, subsection and so on
  if el.level == 1 then
    local zh, en = split_title(pandoc.utils.stringify(el.content))
    return pandoc.RawBlock("latex", "\\papertitle{" .. latex({pandoc.Str(zh)}) .. "}{" .. (en and latex({pandoc.Str(en)}) or "") .. "}")
  end
  local cmd = ({"section", "subsection", "subsubsection"})[el.level - 1] or "paragraph"
  local text = pandoc.utils.stringify(el.content)
  local zh, en = split_title(text)
  local short = latex({pandoc.Str(zh)})
  local full = short
  if en then
    full = short .. "\\headingen{" .. latex({pandoc.Str(en)}) .. "}"
  end
  return pandoc.RawBlock("latex", "\\" .. cmd .. "[" .. short .. "]{" .. full .. "}")
end

-- Block quotes
local function strip_label(para)
  table.remove(para.content, 1)  -- drop the bold 译注 label
  while #para.content > 0 do
    local first = para.content[1]
    if first.t == "Space" then
      table.remove(para.content, 1)
    elseif first.t == "Str" and first.text:find("^　") then
      local rest = first.text:gsub("^　+", "")
      if rest == "" then table.remove(para.content, 1) else first.text = rest; break end
    else
      break
    end
  end
end

function BlockQuote(el)
  local first = el.content[1]
  local env = "quotebox"
  if first and first.t == "Para" and first.content[1] and first.content[1].t == "Strong"
      and pandoc.utils.stringify(first.content[1]) == "译注" then
    env = "yizhubox"
    strip_label(first)
  end
  local blocks = pandoc.List({pandoc.RawBlock("latex", "\\begin{" .. env .. "}")})
  blocks:extend(el.content)
  blocks:insert(pandoc.RawBlock("latex", "\\end{" .. env .. "}"))
  return blocks
end

-- Paragraph level: figures with captions, footnote paragraphs, bold-only paragraphs
local function is_image_para(b)
  return b and b.t == "Para" and #b.content == 1 and b.content[1].t == "Image"
end

local function caption_of(b)
  if b and b.t == "Para" and #b.content >= 1 and b.content[1].t == "Emph" then
    local s = pandoc.utils.stringify(b.content)
    if s:match("^图 %d+：") or s:match("^表 %d+：") then return b.content[1].content end
  end
  return nil
end

local function figure_block(img, cap_inls)
  local src = img.src
  local w = png_width_pt(src) or TEXTWIDTH_PT
  if w > TEXTWIDTH_PT then w = TEXTWIDTH_PT end
  local graphic = string.format("\\includegraphics[width=%.1fpt,height=0.86\\textheight,keepaspectratio]{%s}", w, src)
  local cap = ""
  local is_table = false
  if cap_inls then
    cap = latex(cap_inls)
    is_table = cap:match("^表") ~= nil
    cap = cap:gsub("^([图表] %d+)：", "{\\sffamily\\bfseries %1}\\quad ")
    cap = "\\caption*{" .. cap .. "}"
  end
  local env = is_table and "table" or "figure"
  local body
  if is_table then
    body = cap .. "\n" .. graphic
  else
    body = graphic .. "\n" .. cap
  end
  return pandoc.RawBlock("latex", "\\begin{" .. env .. "}[htbp]\n\\centering\n" .. body .. "\n\\end{" .. env .. "}")
end

local function is_footnote_para(b)
  if b.t ~= "Para" or #b.content < 3 then return false end
  local f = b.content[1]
  return f.t == "Strong" and pandoc.utils.stringify(f):match("^%d+$") and b.content[2].t == "Space"
end

local function is_strong_only(b)
  return b.t == "Para" and #b.content == 1 and b.content[1].t == "Strong"
end

function Blocks(blocks)
  local out = pandoc.List()
  local i = 1
  local in_refs = false
  while i <= #blocks do
    local b = blocks[i]
    if b.t == "RawBlock" and b.text:find("^\\section%[") then
      if in_refs then out:insert(pandoc.RawBlock("latex", "\\end{refs}")); in_refs = false end
      out:insert(b)
      if b.text:find("^\\section%[参考文献%]") then
        out:insert(pandoc.RawBlock("latex", "\\begin{refs}")); in_refs = true
      end
      i = i + 1
    elseif is_image_para(b) then
      local cap = caption_of(blocks[i + 1])
      out:insert(figure_block(b.content[1], cap))
      i = i + (cap and 2 or 1)
      -- When a code block or table follows the crop, place the float first so the text cannot overtake it
      local nxt = blocks[i]
      if nxt and (nxt.t == "CodeBlock" or nxt.t == "Table") then
        out:insert(pandoc.RawBlock("latex", "\\FloatBarrier"))
      end
    elseif is_footnote_para(b) then
      local num = pandoc.utils.stringify(b.content[1])
      local rest = pandoc.List()
      for k = 3, #b.content do rest:insert(b.content[k]) end
      out:insert(pandoc.RawBlock("latex",
        "\\begin{fnpara}\\textsuperscript{" .. num .. "}\\," .. latex(rest) .. "\\end{fnpara}"))
      i = i + 1
    elseif is_strong_only(b) then
      out:insert(pandoc.RawBlock("latex", "\\needpage{5\\baselineskip}"))
      out:insert(b)
      out:insert(pandoc.RawBlock("latex", "\\nopagebreak"))
      i = i + 1
    else
      out:insert(b)
      i = i + 1
    end
  end
  if in_refs then out:insert(pandoc.RawBlock("latex", "\\end{refs}")) end
  return out
end

-- Inline elements first (superscripts, URLs, code), then paragraphs and headings
return {
  {Inlines = Inlines, Code = Code},
  {Blocks = Blocks, BlockQuote = BlockQuote, Header = Header},
}

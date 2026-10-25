"use client";

/** Small, safe Markdown renderer for assistant answers and imported notes. */

import { Fragment, type ReactNode } from "react";

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*\n]+\*|\[[^\]]+\]\([^)\s]+\))/g;
  const parts: ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let index = 0;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index));
    }
    const token = match[0];
    const key = `${keyPrefix}-${index}`;
    if (token.startsWith("**")) {
      parts.push(<strong key={key}>{token.slice(2, -2)}</strong>);
    } else if (token.startsWith("`")) {
      parts.push(<code key={key}>{token.slice(1, -1)}</code>);
    } else if (token.startsWith("*")) {
      parts.push(<em key={key}>{token.slice(1, -1)}</em>);
    } else {
      const link = /\[([^\]]+)\]\(([^)\s]+)\)/.exec(token);
      if (link) {
        const href = link[2];
        const safe = /^(https?:|mailto:)/i.test(href);
        parts.push(
          safe ? (
            <a key={key} href={href} target="_blank" rel="noreferrer noopener">
              {link[1]}
            </a>
          ) : (
            <span key={key}>{link[1]}</span>
          ),
        );
      }
    }
    lastIndex = pattern.lastIndex;
    index += 1;
  }

  if (lastIndex < text.length) parts.push(text.slice(lastIndex));
  return parts;
}

function splitCells(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

interface Block {
  type:
    | "heading"
    | "paragraph"
    | "ul"
    | "ol"
    | "quote"
    | "code"
    | "table"
    | "hr";
  level?: number;
  lines?: string[];
  rows?: string[][];
  header?: string[];
  language?: string;
}

function parseBlocks(source: string): Block[] {
  const lines = source.replace(/\r\n/g, "\n").split("\n");
  const blocks: Block[] = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }

    if (line.trim().startsWith("```")) {
      const language = line.trim().slice(3).trim();
      const body: string[] = [];
      index += 1;
      while (index < lines.length && !lines[index].trim().startsWith("```")) {
        body.push(lines[index]);
        index += 1;
      }
      index += 1;
      blocks.push({ type: "code", lines: body, language });
      continue;
    }

    const heading = /^(#{1,4})\s+(.+)$/.exec(line);
    if (heading) {
      blocks.push({
        type: "heading",
        level: heading[1].length,
        lines: [heading[2]],
      });
      index += 1;
      continue;
    }

    if (/^(\*\s*){3,}$/.test(line.trim()) || /^(-{3,})$/.test(line.trim())) {
      blocks.push({ type: "hr" });
      index += 1;
      continue;
    }

    if (line.trim().startsWith(">")) {
      const quote: string[] = [];
      while (index < lines.length && lines[index].trim().startsWith(">")) {
        quote.push(lines[index].trim().replace(/^>\s?/, ""));
        index += 1;
      }
      blocks.push({ type: "quote", lines: quote });
      continue;
    }

    if (/^\s*[-*+]\s+/.test(line)) {
      const items: string[] = [];
      while (index < lines.length && /^\s*[-*+]\s+/.test(lines[index])) {
        items.push(lines[index].replace(/^\s*[-*+]\s+/, ""));
        index += 1;
      }
      blocks.push({ type: "ul", lines: items });
      continue;
    }

    if (/^\s*\d+[.)]\s+/.test(line)) {
      const items: string[] = [];
      while (index < lines.length && /^\s*\d+[.)]\s+/.test(lines[index])) {
        items.push(lines[index].replace(/^\s*\d+[.)]\s+/, ""));
        index += 1;
      }
      blocks.push({ type: "ol", lines: items });
      continue;
    }

    const next = lines[index + 1];
    if (
      line.includes("|") &&
      next !== undefined &&
      /^\s*\|?[\s:|-]+\|[\s:|-]*$/.test(next) &&
      next.includes("-")
    ) {
      const header = splitCells(line);
      index += 2;
      const rows: string[][] = [];
      while (index < lines.length && lines[index].includes("|") && lines[index].trim()) {
        rows.push(splitCells(lines[index]));
        index += 1;
      }
      blocks.push({ type: "table", header, rows });
      continue;
    }

    const paragraph: string[] = [line];
    index += 1;
    while (
      index < lines.length &&
      lines[index].trim() &&
      !/^(#{1,4}\s|>\s*|\s*[-*+]\s+|\s*\d+[.)]\s+|```)/.test(lines[index])
    ) {
      paragraph.push(lines[index]);
      index += 1;
    }
    blocks.push({ type: "paragraph", lines: paragraph });
  }

  return blocks;
}

export function Markdown({ source }: { source: string }) {
  if (!source.trim()) return null;
  const blocks = parseBlocks(source);

  return (
    <div className="md">
      {blocks.map((block, blockIndex) => {
        const key = `block-${blockIndex}`;
        switch (block.type) {
          case "heading": {
            const text = block.lines?.[0] ?? "";
            const content = renderInline(text, key);
            if (block.level === 1) return <h1 key={key}>{content}</h1>;
            if (block.level === 2) return <h2 key={key}>{content}</h2>;
            if (block.level === 3) return <h3 key={key}>{content}</h3>;
            return <h4 key={key}>{content}</h4>;
          }
          case "paragraph":
            return (
              <p key={key}>
                {(block.lines ?? []).map((line, lineIndex) => (
                  <Fragment key={`${key}-${lineIndex}`}>
                    {lineIndex > 0 ? <br /> : null}
                    {renderInline(line, `${key}-${lineIndex}`)}
                  </Fragment>
                ))}
              </p>
            );
          case "ul":
            return (
              <ul key={key}>
                {(block.lines ?? []).map((item, itemIndex) => (
                  <li key={`${key}-${itemIndex}`}>
                    {renderInline(item, `${key}-${itemIndex}`)}
                  </li>
                ))}
              </ul>
            );
          case "ol":
            return (
              <ol key={key}>
                {(block.lines ?? []).map((item, itemIndex) => (
                  <li key={`${key}-${itemIndex}`}>
                    {renderInline(item, `${key}-${itemIndex}`)}
                  </li>
                ))}
              </ol>
            );
          case "quote":
            return (
              <blockquote key={key}>
                {(block.lines ?? []).map((line, lineIndex) => (
                  <Fragment key={`${key}-${lineIndex}`}>
                    {lineIndex > 0 ? <br /> : null}
                    {renderInline(line, `${key}-${lineIndex}`)}
                  </Fragment>
                ))}
              </blockquote>
            );
          case "code":
            return (
              <pre key={key}>
                <code>{(block.lines ?? []).join("\n")}</code>
              </pre>
            );
          case "table":
            return (
              <div className="md-table-wrap" key={key}>
                <table>
                  <thead>
                    <tr>
                      {(block.header ?? []).map((cell, cellIndex) => (
                        <th key={`${key}-h-${cellIndex}`}>
                          {renderInline(cell, `${key}-h-${cellIndex}`)}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {(block.rows ?? []).map((row, rowIndex) => (
                      <tr key={`${key}-r-${rowIndex}`}>
                        {(block.header ?? []).map((_, cellIndex) => (
                          <td key={`${key}-r-${rowIndex}-${cellIndex}`}>
                            {renderInline(
                              row[cellIndex] ?? "",
                              `${key}-r-${rowIndex}-${cellIndex}`,
                            )}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          case "hr":
            return <hr key={key} />;
          default:
            return null;
        }
      })}
    </div>
  );
}

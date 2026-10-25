import Foundation

/// 服务器回答里常见的 Markdown 子集：标题、段落、有序/无序列表、引用、代码块、分隔线。
/// 只做展示解析，不改写原文；解析不了的内容按段落原样显示，不吞字符、不伪造结构。
enum MarkdownBlock: Equatable {
    case heading(level: Int, text: String)
    case paragraph(String)
    case bullet(text: String, depth: Int)
    case ordered(number: Int, text: String, depth: Int)
    case quote(String)
    case code(String)
    case rule
}

enum MarkdownParser {
    static func parse(_ raw: String) -> [MarkdownBlock] {
        var blocks: [MarkdownBlock] = []
        var paragraph: [String] = []
        var codeLines: [String] = []
        var inCode = false

        func flushParagraph() {
            guard !paragraph.isEmpty else { return }
            blocks.append(.paragraph(paragraph.joined(separator: "\n")))
            paragraph.removeAll()
        }

        let normalized = raw
            .replacingOccurrences(of: "\r\n", with: "\n")
            .replacingOccurrences(of: "\r", with: "\n")

        for line in normalized.components(separatedBy: "\n") {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if trimmed.hasPrefix("```") {
                if inCode {
                    blocks.append(.code(codeLines.joined(separator: "\n")))
                    codeLines.removeAll()
                    inCode = false
                } else {
                    flushParagraph()
                    inCode = true
                }
                continue
            }
            if inCode {
                codeLines.append(line)
                continue
            }
            if trimmed.isEmpty {
                flushParagraph()
                continue
            }
            if trimmed == "---" || trimmed == "***" || trimmed == "___" {
                flushParagraph()
                blocks.append(.rule)
                continue
            }
            if let heading = headingBlock(trimmed) {
                flushParagraph()
                blocks.append(heading)
                continue
            }
            if trimmed.hasPrefix(">") {
                flushParagraph()
                blocks.append(.quote(quoteText(trimmed)))
                continue
            }
            if let bullet = bulletBlock(line) {
                flushParagraph()
                blocks.append(bullet)
                continue
            }
            if let ordered = orderedBlock(line) {
                flushParagraph()
                blocks.append(ordered)
                continue
            }
            paragraph.append(trimmed)
        }

        if inCode, !codeLines.isEmpty {
            blocks.append(.code(codeLines.joined(separator: "\n")))
        }
        flushParagraph()
        return blocks
    }

    /// 行内标记（加粗、斜体、行内代码、链接）交给系统解析；块级结构由上面逐行处理。
    static func inline(_ text: String) -> AttributedString {
        if let attributed = try? AttributedString(
            markdown: text,
            options: .init(
                interpretedSyntax: .inlineOnlyPreservingWhitespace,
                failurePolicy: .returnPartiallyParsedIfPossible
            )
        ) {
            return attributed
        }
        return AttributedString(text)
    }

    // MARK: - 单行识别

    private static func headingBlock(_ trimmed: String) -> MarkdownBlock? {
        var level = 0
        var index = trimmed.startIndex
        while index < trimmed.endIndex, trimmed[index] == "#", level < 7 {
            level += 1
            index = trimmed.index(after: index)
        }
        guard (1...6).contains(level), index < trimmed.endIndex, trimmed[index] == " " else { return nil }
        let text = trimmed[index...].trimmingCharacters(in: .whitespaces)
        guard !text.isEmpty else { return nil }
        return .heading(level: level, text: text)
    }

    private static func quoteText(_ trimmed: String) -> String {
        var content = trimmed.dropFirst()
        if content.hasPrefix(" ") { content = content.dropFirst() }
        return String(content).trimmingCharacters(in: .whitespaces)
    }

    private static func bulletBlock(_ line: String) -> MarkdownBlock? {
        let (indent, rest) = splitIndent(line)
        guard let marker = rest.first, "-*+".contains(marker) else { return nil }
        let afterMarker = rest.dropFirst()
        guard afterMarker.first == " " else { return nil }
        let text = afterMarker.trimmingCharacters(in: .whitespaces)
        guard !text.isEmpty else { return nil }
        return .bullet(text: text, depth: min(indent / 2, 3))
    }

    private static func orderedBlock(_ line: String) -> MarkdownBlock? {
        let (indent, rest) = splitIndent(line)
        var cursor = rest.startIndex
        var digits = ""
        while cursor < rest.endIndex, rest[cursor].isNumber, digits.count < 3 {
            digits.append(rest[cursor])
            cursor = rest.index(after: cursor)
        }
        guard !digits.isEmpty, cursor < rest.endIndex else { return nil }
        guard rest[cursor] == "." || rest[cursor] == ")" else { return nil }
        let afterMarker = rest.index(after: cursor)
        guard afterMarker < rest.endIndex, rest[afterMarker] == " " else { return nil }
        let text = rest[rest.index(after: afterMarker)...].trimmingCharacters(in: .whitespaces)
        guard !text.isEmpty, let number = Int(digits) else { return nil }
        return .ordered(number: number, text: text, depth: min(indent / 2, 3))
    }

    private static func splitIndent(_ line: String) -> (indent: Int, rest: Substring) {
        var indent = 0
        var index = line.startIndex
        while index < line.endIndex {
            switch line[index] {
            case " ": indent += 1
            case "\t": indent += 2
            default: return (indent, line[index...])
            }
            index = line.index(after: index)
        }
        return (indent, line[index...])
    }
}

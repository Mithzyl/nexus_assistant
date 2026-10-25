import SwiftUI

/// 助手回答的 Markdown 展示。只影响排版，不改写服务器保存的原文。
struct MarkdownAnswerView: View {
    let text: String

    private var blocks: [MarkdownBlock] { MarkdownParser.parse(text) }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(Array(blocks.enumerated()), id: \.offset) { _, block in
                blockView(block)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    @ViewBuilder
    private func blockView(_ block: MarkdownBlock) -> some View {
        switch block {
        case .heading(let level, let text):
            Text(MarkdownParser.inline(text))
                .font(headingFont(level))
                .foregroundStyle(Palette.ink)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, level <= 2 ? 2 : 0)

        case .paragraph(let text):
            Text(MarkdownParser.inline(text))
                .font(.bodyText)
                .foregroundStyle(Palette.ink)
                .lineSpacing(3)
                .fixedSize(horizontal: false, vertical: true)

        case .bullet(let text, let depth):
            listRow(marker: "•", depth: depth, text: text)

        case .ordered(let number, let text, let depth):
            listRow(marker: "\(number).", depth: depth, text: text)

        case .quote(let text):
            Text(MarkdownParser.inline(text))
                .font(.bodyText)
                .foregroundStyle(Palette.muted)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.leading, 10)
                .overlay(alignment: .leading) {
                    Rectangle()
                        .fill(Palette.fogSoft)
                        .frame(width: 3)
                }

        case .code(let text):
            Text(text)
                .font(.system(.footnote, design: .monospaced))
                .foregroundStyle(Palette.ink)
                .padding(10)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Palette.mist, in: RoundedRectangle(cornerRadius: 10, style: .continuous))

        case .rule:
            Rectangle()
                .fill(Palette.line)
                .frame(height: 0.5)
        }
    }

    private func listRow(marker: String, depth: Int, text: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 7) {
            Text(marker)
                .font(.bodyText)
                .foregroundStyle(Palette.muted)
                .frame(minWidth: 16, alignment: .trailing)
            Text(MarkdownParser.inline(text))
                .font(.bodyText)
                .foregroundStyle(Palette.ink)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.leading, CGFloat(min(depth, 3)) * 16)
    }

    private func headingFont(_ level: Int) -> Font {
        switch level {
        case 1: return .system(.title3).weight(.semibold)
        case 2: return .system(.headline)
        default: return .system(.subheadline).weight(.semibold)
        }
    }
}

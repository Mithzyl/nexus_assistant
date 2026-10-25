import SwiftUI
import UIKit

/// V3 design tokens: fog blue on warm white, solid content, translucent chrome only.
/// Every token has a dark variant so the app follows the system appearance.
enum Palette {
    // Text
    static let ink = Color(light: 0x293E4B, dark: 0xE9EFF3)
    static let muted = Color(light: 0x5F7481, dark: 0xAEBEC8)
    static let faint = Color(light: 0x8A9AA4, dark: 0x86959E)
    // Accent
    static let fog = Color(light: 0x527C98, dark: 0x93B9D1)
    static let fogDeep = Color(light: 0x3C5F73, dark: 0xA9CBE0)
    static let fogSoft = Color(light: 0xD5E6F2, dark: 0x263541)
    /// Filled accent surface; always paired with white text.
    static let accentFill = Color(light: 0x3C5F73, dark: 0x4C7791)
    // Surfaces
    static let warm = Color(light: 0xFAF9F4, dark: 0x111619)
    static let card = Color(light: 0xFFFFFF, dark: 0x1A2126)
    static let mist = Color(light: 0xE9F2F7, dark: 0x1C2932)
    static let line = Color(light: 0xE2E6E1, dark: 0x2B353A)
    static let neutralFill = Color(light: 0xF1F1EC, dark: 0x252C30)
    // Warm / semantic
    static let amber = Color(light: 0x9A6B2F, dark: 0xD7AB69)
    static let amberSoft = Color(light: 0xF6EBD9, dark: 0x33291B)
    static let green = Color(light: 0x3F7355, dark: 0x8AC2A0)
    static let greenSoft = Color(light: 0xE4EFE6, dark: 0x1F2C24)
    static let red = Color(light: 0x9B4A3F, dark: 0xDBA49A)
    static let redSoft = Color(light: 0xF6E6E2, dark: 0x352220)
}

extension Color {
    init(hex: UInt32, alpha: Double = 1) {
        self.init(
            .sRGB,
            red: Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255,
            opacity: alpha
        )
    }

    init(light: UInt32, dark: UInt32) {
        self.init(uiColor: UIColor { traits in
            UIColor(rgb: traits.userInterfaceStyle == .dark ? dark : light)
        })
    }
}

private extension UIColor {
    convenience init(rgb: UInt32) {
        self.init(
            red: CGFloat((rgb >> 16) & 0xFF) / 255,
            green: CGFloat((rgb >> 8) & 0xFF) / 255,
            blue: CGFloat(rgb & 0xFF) / 255,
            alpha: 1
        )
    }
}

/// Text styles, not fixed sizes, so Dynamic Type and accessibility sizes work.
extension Font {
    static let screenTitle = Font.system(.title, design: .serif).weight(.medium)
    static let sectionTitle = Font.system(.title3).weight(.semibold)
    static let cardTitle = Font.headline
    static let bodyText = Font.subheadline
    static let captionText = Font.footnote
    static let microLabel = Font.caption.weight(.medium)
}

enum Spacing {
    static let screen: CGFloat = 22
    static let card: CGFloat = 16
    static let stack: CGFloat = 14
    static let group: CGFloat = 24
    static let section: CGFloat = 28
}

/// Native Liquid Glass on iOS 26+, system thin material before that.
extension View {
    @ViewBuilder
    func chromeGlass<S: Shape>(in shape: S) -> some View {
        if #available(iOS 26.0, *) {
            self.glassEffect(.regular, in: shape)
        } else {
            self.background(.ultraThinMaterial, in: shape)
        }
    }
}

/// A solid card. Glass is reserved for navigation, entry points and small controls.
struct SurfaceCard<Content: View>: View {
    var padding: CGFloat = Spacing.card
    var tint: Color = Palette.card
    @ViewBuilder var content: () -> Content

    var body: some View {
        content()
            .padding(padding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(tint)
            .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 18, style: .continuous)
                    .strokeBorder(Palette.line.opacity(0.7), lineWidth: 0.5)
            )
    }
}

struct PrimaryButton: View {
    let title: String
    var enabled: Bool = true
    var identifier: String? = nil
    var action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 8) {
                Text(title)
                    .font(.system(.subheadline, weight: .semibold))
                Image(systemName: "arrow.right")
                    .font(.system(.footnote, weight: .semibold))
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 14)
            .foregroundStyle(enabled ? Color.white : Palette.faint)
            .background(enabled ? Palette.accentFill : Palette.fogSoft.opacity(0.5))
            .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        }
        .buttonStyle(.plain)
        .disabled(!enabled)
        .accessibilityIdentifier(identifier ?? title)
    }
}

struct SecondaryButton: View {
    let title: String
    var action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title)
                .font(.system(.subheadline, weight: .medium))
                .frame(maxWidth: .infinity)
                .padding(.vertical, 13)
                .foregroundStyle(Palette.fogDeep)
                .background(Palette.mist)
                .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        }
        .buttonStyle(.plain)
    }
}

struct SectionHeading: View {
    let title: String
    var trailing: String? = nil

    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            Text(title)
                .font(.sectionTitle)
                .foregroundStyle(Palette.ink)
            Spacer()
            if let trailing {
                Text(trailing)
                    .font(.captionText)
                    .foregroundStyle(Palette.faint)
            }
        }
    }
}

struct TagPill: View {
    let text: String
    var tone: Tone = .neutral

    enum Tone { case neutral, fog, amber, green, red }

    private var fill: Color {
        switch tone {
        case .neutral: return Palette.neutralFill
        case .fog: return Palette.mist
        case .amber: return Palette.amberSoft
        case .green: return Palette.greenSoft
        case .red: return Palette.redSoft
        }
    }

    private var textColor: Color {
        switch tone {
        case .neutral: return Palette.muted
        case .fog: return Palette.fogDeep
        case .amber: return Palette.amber
        case .green: return Palette.green
        case .red: return Palette.red
        }
    }

    var body: some View {
        Text(text)
            .font(.microLabel)
            .padding(.horizontal, 9)
            .padding(.vertical, 4)
            .foregroundStyle(textColor)
            .background(fill)
            .clipShape(Capsule())
    }
}

struct RowLink: View {
    let icon: String
    let title: String
    let detail: String
    var status: String? = nil
    var action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 12) {
                Image(systemName: icon)
                    .font(.system(size: 16, weight: .regular))
                    .foregroundStyle(Palette.fog)
                    .frame(width: 30, height: 30)
                    .background(Palette.mist)
                    .clipShape(RoundedRectangle(cornerRadius: 9, style: .continuous))
                VStack(alignment: .leading, spacing: 3) {
                    Text(title)
                        .font(.cardTitle)
                        .foregroundStyle(Palette.ink)
                        .multilineTextAlignment(.leading)
                    Text(detail)
                        .font(.captionText)
                        .foregroundStyle(Palette.muted)
                        .multilineTextAlignment(.leading)
                }
                Spacer(minLength: 8)
                if let status {
                    Text(status)
                        .font(.microLabel)
                        .foregroundStyle(Palette.faint)
                }
                Image(systemName: "chevron.right")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(Palette.faint)
            }
            .padding(.vertical, 12)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

struct DividerLine: View {
    var body: some View {
        Rectangle()
            .fill(Palette.line)
            .frame(height: 0.5)
    }
}

struct ScreenHeader: View {
    let title: String
    var subtitle: String? = nil
    var onCapture: (() -> Void)? = nil

    var body: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 3) {
                Text(title)
                    .font(.screenTitle)
                    .foregroundStyle(Palette.ink)
                if let subtitle {
                    Text(subtitle)
                        .font(.captionText)
                        .foregroundStyle(Palette.muted)
                }
            }
            Spacer()
            if let onCapture {
                Button(action: onCapture) {
                    Image(systemName: "plus")
                        .font(.system(size: 16, weight: .medium))
                        .foregroundStyle(Palette.fogDeep)
                        .frame(width: 44, height: 44)
                        .contentShape(Circle())
                }
                .chromeGlass(in: Circle())
                .accessibilityLabel("交给助手")
                .buttonStyle(.plain)
            }
        }
        .padding(.horizontal, Spacing.screen)
        .padding(.top, 8)
        .padding(.bottom, 10)
    }
}

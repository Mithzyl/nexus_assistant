import SwiftUI

/// 编辑通勤：原生分组表单。默认只露出路线、时间、提醒；阈值等技术项收进“更多设置”。
struct CommuteSettingsView: View {
    @EnvironmentObject private var store: CommuteStore
    @EnvironmentObject private var push: PushNotificationManager
    @Environment(\.dismiss) private var dismiss
    @Environment(\.openURL) private var openURL

    private enum PlaceEditing: String, Identifiable {
        case origin, destination
        var id: String { rawValue }
    }

    private enum RepeatMode: String, CaseIterable, Identifiable {
        case daily = "每天", workdays = "工作日", custom = "自定义"
        var id: String { rawValue }
    }

    private let bufferChoices = [5, 10, 15, 20, 30]

    @State private var draft = CommutePlanDraft.empty()
    @State private var original: CommutePlanDraft?
    @State private var loaded = false
    @State private var editingPlace: PlaceEditing?
    @State private var repeatMode: RepeatMode = .workdays
    @State private var saving = false
    @State private var showValidation = false
    @State private var showCheckTime = false
    @State private var showMore = false
    @State private var showCancelConfirm = false
    @State private var showDeleteConfirm = false
    @State private var showLocalDraftConfirm = false
    @State private var showLogin = false

    private var isNew: Bool { store.plan == nil }
    private var isDirty: Bool { original != draft }

    private var problems: [String] { CommuteValidation.problems(in: draft) }

    var body: some View {
        Group {
            if loaded {
                form
            } else {
                ProgressView("正在读取设置…")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .background(Palette.warm)
            }
        }
        .navigationTitle(isNew ? "添加通勤" : "编辑通勤")
        .navigationBarTitleDisplayMode(.inline)
        .navigationBarBackButtonHidden(true)
        .toolbar {
            ToolbarItem(placement: .cancellationAction) {
                Button("取消") { attemptCancel() }
            }
            ToolbarItem(placement: .confirmationAction) {
                Button {
                    save()
                } label: {
                    if saving { ProgressView() } else { Text("保存") }
                }
                .disabled(saving)
            }
        }
        .task {
            #if DEBUG
            if ProcessInfo.processInfo.environment["START_MORE"] == "1" { showMore = true }
            #endif
            guard !loaded else { return }
            if store.plan == nil { await store.load() }
            if let plan = store.plan {
                draft = CommutePlanDraft(plan: plan)
            } else if let local = store.localStore.loadDraft() {
                draft = local
            }
            repeatMode = repeatMode(for: draft.weekdays)
            original = draft
            #if DEBUG
            // 仅 UI 验证用：模拟用户改了一个不同的到达时间，保存仍走真实流程。
            if let override = ProcessInfo.processInfo.environment["COMMUTE_TEST_ARRIVAL"],
               !override.isEmpty {
                draft.arrivalLocalTime = override
            }
            #endif
            loaded = true
        }
        .sheet(item: $editingPlace) { which in
            PlacePickerView(initial: which == .origin ? draft.origin : draft.destination) { point in
                if which == .origin { draft.origin = point } else { draft.destination = point }
            }
        }
        .sheet(isPresented: $showLogin) {
            CommuteLoginSheet().environmentObject(store)
        }
        .confirmationDialog("放弃修改？", isPresented: $showCancelConfirm, titleVisibility: .visible) {
            Button("放弃修改", role: .destructive) { dismiss() }
            Button("继续编辑", role: .cancel) {}
        } message: {
            Text("尚未保存的修改会丢失。")
        }
        .confirmationDialog("删除这条通勤？", isPresented: $showDeleteConfirm, titleVisibility: .visible) {
            Button("删除并停止后续提醒", role: .destructive) {
                Task {
                    if await store.deleteCurrentPlan() { dismiss() }
                }
            }
            Button("取消", role: .cancel) {}
        } message: {
            Text("服务器将停止按这条设置生成后续提醒。已生成的建议不会追溯删除。")
        }
        .confirmationDialog("暂时无法保存到服务器", isPresented: $showLocalDraftConfirm, titleVisibility: .visible) {
            Button("保存为本地草稿") {
                store.saveLocalDraft(draft)
                dismiss()
            }
            Button("取消", role: .cancel) {}
        } message: {
            Text("本地草稿不会开启服务器提醒；登录后还需要再保存一次。")
        }
    }

    // MARK: - 表单

    private var form: some View {
        Form {
            if let conflict = store.conflictNotice {
                Section {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(conflict).font(.captionText).foregroundStyle(Palette.red)
                        HStack(spacing: 12) {
                            Button("重新加载") {
                                Task {
                                    await store.reloadFromServer()
                                    if let plan = store.plan {
                                        draft = CommutePlanDraft(plan: plan)
                                        original = draft
                                        store.conflictNotice = nil
                                    }
                                }
                            }
                            .font(.system(.subheadline, weight: .semibold))
                            Button("保留草稿") { store.conflictNotice = nil }
                                .font(.system(.subheadline, weight: .semibold))
                        }
                    }
                }
            }

            routeSection
            scheduleSection
            reminderSection
            moreSection
            summarySection

            if !store.isSignedIn {
                Section {
                    Button("登录服务器") { showLogin = true }
                    Text("未登录时只能保存本地草稿，服务器不会生成提醒。")
                        .font(.captionText)
                        .foregroundStyle(Palette.faint)
                }
            }

            if !isNew {
                Section {
                    Button(role: .destructive) {
                        showDeleteConfirm = true
                    } label: {
                        Text("删除通勤").frame(minHeight: 44)
                    }
                } footer: {
                    Text("删除会停止后续提醒，并从服务器移除这条设置。")
                }
            }
        }
        .scrollContentBackground(.hidden)
        .background(Palette.warm)
        .tint(Palette.fog)
        .alert("无法保存", isPresented: Binding(
            get: { showValidation },
            set: { showValidation = $0 }
        )) {
            Button("好", role: .cancel) {}
        } message: {
            Text(problems.joined(separator: "\n"))
        }
    }

    // MARK: - 路线

    private var routeSection: some View {
        Section {
            VStack(alignment: .leading, spacing: 4) {
                TextField("名称，例如：上班", text: $draft.name)
                    .textInputAutocapitalization(.never)
                if showValidation && draft.name.trimmingCharacters(in: .whitespaces).isEmpty {
                    Text("请填写名称").font(.captionText).foregroundStyle(Palette.red)
                }
            }

            Button { editingPlace = .origin } label: {
                placeRow(icon: "circle", tint: Palette.fog, label: "起点", point: draft.origin)
            }
            .buttonStyle(.plain)

            Button { editingPlace = .destination } label: {
                placeRow(icon: "mappin.circle.fill", tint: Palette.amber, label: "终点", point: draft.destination)
            }
            .buttonStyle(.plain)

            Button {
                let origin = draft.origin
                draft.origin = draft.destination
                draft.destination = origin
            } label: {
                Label("交换起点和终点", systemImage: "arrow.up.arrow.down")
                    .font(.subheadline)
                    .frame(minHeight: 44)
            }
            .buttonStyle(.plain)

            if showValidation {
                if draft.origin.label.isEmpty {
                    Text("请选择起点").font(.captionText).foregroundStyle(Palette.red)
                }
                if draft.destination.label.isEmpty {
                    Text("请选择终点").font(.captionText).foregroundStyle(Palette.red)
                }
            }
        } header: {
            Text("路线")
        }
    }

    private func placeRow(icon: String, tint: Color, label: String, point: GeoPoint) -> some View {
        HStack(spacing: 12) {
            Image(systemName: icon)
                .foregroundStyle(tint)
                .frame(width: 22)
            VStack(alignment: .leading, spacing: 1) {
                Text(label).font(.captionText).foregroundStyle(Palette.muted)
                Text(point.label.isEmpty ? "选择地点" : point.label)
                    .font(.bodyText)
                    .foregroundStyle(point.label.isEmpty ? Palette.faint : Palette.ink)
            }
            Spacer()
            Image(systemName: "chevron.right")
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(Palette.faint)
        }
        .frame(minHeight: 44)
        .contentShape(Rectangle())
    }

    // MARK: - 出行安排

    private var scheduleSection: some View {
        Section {
            DatePicker("到达时间", selection: arrivalBinding, displayedComponents: .hourAndMinute)

            Picker("重复", selection: $repeatMode) {
                ForEach(RepeatMode.allCases) { mode in
                    Text(mode.rawValue).tag(mode)
                }
            }
            .onChange(of: repeatMode) { _, mode in
                applyRepeat(mode)
            }

            if repeatMode == .custom {
                HStack(spacing: 6) {
                    ForEach(Weekday.allCases) { day in
                        Button {
                            toggle(day.rawValue)
                        } label: {
                            Text(day.shortTitle)
                                .font(.subheadline)
                                .foregroundStyle(draft.weekdays.contains(day.rawValue) ? Color.white : Palette.ink)
                                .frame(width: 36, height: 40)
                                .background(draft.weekdays.contains(day.rawValue) ? Palette.accentFill : Palette.mist)
                                .clipShape(RoundedRectangle(cornerRadius: 9, style: .continuous))
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel("周\(day.shortTitle)")
                    }
                }
            }

            Picker("交通方式", selection: $draft.transportMode) {
                ForEach(TransportMode.allCases) { mode in
                    Text(mode.title).tag(mode)
                }
            }
            .pickerStyle(.segmented)

            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Text("提前到达")
                    Spacer()
                    Text("\(draft.bufferMinutes) 分钟").foregroundStyle(Palette.muted)
                }
                HStack(spacing: 6) {
                    ForEach(bufferChoices, id: \.self) { value in
                        Button {
                            draft.bufferMinutes = value
                        } label: {
                            Text("\(value)").font(.subheadline)
                                .padding(.horizontal, 10)
                                .frame(minHeight: 36)
                                .background(draft.bufferMinutes == value ? Palette.accentFill : Palette.mist)
                                .foregroundStyle(draft.bufferMinutes == value ? Color.white : Palette.ink)
                                .clipShape(Capsule())
                        }
                        .buttonStyle(.plain)
                    }
                    Stepper("", value: $draft.bufferMinutes, in: 0...120, step: 5)
                        .labelsHidden()
                }
            }

            if showValidation && draft.weekdays.isEmpty {
                Text("至少选择一天").font(.captionText).foregroundStyle(Palette.red)
            }
            if showValidation && !timeOrderValid {
                Text("开始检查时间必须早于到达时间；跨午夜暂不支持")
                    .font(.captionText)
                    .foregroundStyle(Palette.red)
            }
        } header: {
            Text("出行安排")
        } footer: {
            Text("“工作日”指周一至周五，不包含法定节假日与调休。")
        }
    }

    // MARK: - 提醒

    private var reminderSection: some View {
        Section {
            Toggle("通勤提醒", isOn: $draft.notifyEnabled)

            Button {
                showCheckTime = true
            } label: {
                HStack {
                    Text(checkSummary)
                    Spacer()
                    Image(systemName: "chevron.right")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(Palette.faint)
                }
                .frame(minHeight: 44)
            }
            .buttonStyle(.plain)

            if showCheckTime {
                DatePicker("开始检查时间", selection: checkBinding, displayedComponents: .hourAndMinute)
            }

            permissionRow
        } header: {
            Text("提醒")
        } footer: {
            Text("开启后，所选地点坐标与日程会通过 PA 后端用于天气查询、路线估算和提醒；不会在后台持续定位。")
        }
    }

    private var checkSummary: String {
        "\(repeatSummary) \(draft.checkLocalTime) 开始检查天气和路况"
    }

    private var repeatSummary: String {
        switch repeatMode {
        case .daily: return "每天"
        case .workdays: return "工作日"
        case .custom:
            let days = draft.weekdays.sorted().map { "周" + Weekday(rawValue: $0)!.shortTitle }
            return days.isEmpty ? "未选择日期" : days.joined(separator: "、")
        }
    }

    @ViewBuilder
    private var permissionRow: some View {
        switch push.systemAuthorization {
        case .authorized, .provisional:
            Label("系统通知已允许", systemImage: "checkmark.circle")
                .font(.captionText)
                .foregroundStyle(Palette.green)
        case .denied:
            VStack(alignment: .leading, spacing: 4) {
                Text("系统通知已关闭，服务器提醒无法送达。").font(.captionText)
                Button("前往系统设置") {
                    if let url = URL(string: UIApplication.openSettingsURLString) { openURL(url) }
                }
                .font(.subheadline)
            }
        default:
            VStack(alignment: .leading, spacing: 4) {
                Text("尚未允许系统通知。保存通勤不受影响，但收不到提醒。").font(.captionText)
                Button("开启通知") {
                    Task {
                        await push.requestAuthorization()
                        store.updatePermission(push.mappedPermission)
                    }
                }
                .font(.subheadline)
            }
        }
    }

    // MARK: - 更多设置

    private var moreSection: some View {
        Section {
            DisclosureGroup(isExpanded: $showMore) {
                Stepper(value: $draft.rainExtraBufferMinutes, in: 0...60, step: 5) {
                    Text("雨天额外预留：\(draft.rainExtraBufferMinutes) 分钟")
                }

                VStack(alignment: .leading, spacing: 4) {
                    HStack {
                        Text("降雨判断阈值")
                        Spacer()
                        Text("\(thresholdPercent)%").foregroundStyle(Palette.muted)
                    }
                    Slider(value: thresholdBinding, in: 0...100, step: 5)
                }

                HStack {
                    Text("计划时区")
                    Spacer()
                    Text(draft.timezone).foregroundStyle(Palette.muted)
                }

                Toggle("暂停此通勤", isOn: Binding(
                    get: { !draft.enabled },
                    set: { draft.enabled = !$0 }
                ))
            } label: {
                Text("更多设置")
                    .font(.subheadline)
            }
        } footer: {
            if showMore {
                Text("雨天额外预留是在“提前到达”之外再加的时间；设为 0 表示不加。暂停通勤与关闭推送不同：暂停后不再按此计划生成提醒。")
            }
        }
    }

    // MARK: - 摘要

    private var summarySection: some View {
        Section {
            Text(summaryText)
                .font(.captionText)
                .foregroundStyle(Palette.muted)
                .fixedSize(horizontal: false, vertical: true)
        } header: {
            Text("当前配置")
        } footer: {
            Text(isNew ? "尚未保存。" : "修改后需要保存才会生效；旧建议不会用于新路线。")
        }
    }

    private var summaryText: String {
        let destination = draft.destination.label.isEmpty ? "终点" : draft.destination.label
        return "\(repeatSummary)，\(draft.arrivalLocalTime) 前到\(destination)；\(draft.transportMode.title)，提前预留 \(draft.bufferMinutes) 分钟" +
               (draft.rainExtraBufferMinutes > 0 ? "，达到降雨阈值时再预留 \(draft.rainExtraBufferMinutes) 分钟。" : "。")
    }

    // MARK: - 绑定与校验

    private var timezone: TimeZone { TimeZone(identifier: draft.timezone) ?? .current }

    private var arrivalBinding: Binding<Date> {
        Binding(
            get: { Self.date(from: draft.arrivalLocalTime, timezone: timezone) },
            set: { draft.arrivalLocalTime = Self.string(from: $0, timezone: timezone) }
        )
    }

    private var checkBinding: Binding<Date> {
        Binding(
            get: { Self.date(from: draft.checkLocalTime, timezone: timezone) },
            set: { draft.checkLocalTime = Self.string(from: $0, timezone: timezone) }
        )
    }

    private var thresholdBinding: Binding<Double> {
        Binding(
            get: { (draft.rainProbabilityThreshold * 100).rounded() },
            set: { draft.rainProbabilityThreshold = $0 / 100 }
        )
    }

    private var thresholdPercent: Int { Int((draft.rainProbabilityThreshold * 100).rounded()) }

    private var timeOrderValid: Bool {
        guard let arrival = CommuteValidation.minutes(draft.arrivalLocalTime),
              let check = CommuteValidation.minutes(draft.checkLocalTime) else { return false }
        return check < arrival
    }

    private func repeatMode(for weekdays: [Int]) -> RepeatMode {
        let set = Set(weekdays)
        if set == Set(1...7) { return .daily }
        if set == Set(1...5) { return .workdays }
        return .custom
    }

    private func applyRepeat(_ mode: RepeatMode) {
        switch mode {
        case .daily: draft.weekdays = Array(1...7)
        case .workdays: draft.weekdays = Array(1...5)
        case .custom:
            if draft.weekdays.isEmpty { draft.weekdays = [1, 2, 3, 4, 5] }
        }
    }

    private func toggle(_ day: Int) {
        if let index = draft.weekdays.firstIndex(of: day) {
            draft.weekdays.remove(at: index)
        } else {
            draft.weekdays.append(day)
            draft.weekdays.sort()
        }
        repeatMode = .custom
    }

    static func date(from hhmm: String, timezone: TimeZone) -> Date {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = timezone
        formatter.dateFormat = "HH:mm"
        return formatter.date(from: hhmm) ?? Date()
    }

    static func string(from date: Date, timezone: TimeZone) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = timezone
        formatter.dateFormat = "HH:mm"
        return formatter.string(from: date)
    }

    // MARK: - 提交

    private func attemptCancel() {
        if isDirty { showCancelConfirm = true } else { dismiss() }
    }

    private func save() {
        guard !saving else { return }
        if !store.isSignedIn {
            showLocalDraftConfirm = true
            return
        }
        guard problems.isEmpty else {
            showValidation = true
            return
        }
        saving = true
        Task {
            let ok = await store.save(draft: draft)
            saving = false
            if ok { dismiss() }
        }
    }
}

struct CommuteLoginSheet: View {
    @EnvironmentObject private var store: CommuteStore
    @Environment(\.dismiss) private var dismiss
    @State private var email = ""
    @State private var password = ""
    @State private var busy = false

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 16) {
                Text("登录后由服务器生成通勤提醒。演示构建不需要登录。")
                    .font(.captionText)
                    .foregroundStyle(Palette.muted)

                TextField("邮箱", text: $email)
                    .textContentType(.emailAddress)
                    .keyboardType(.emailAddress)
                    .textInputAutocapitalization(.never)
                    .padding(12)
                    .background(Palette.card, in: RoundedRectangle(cornerRadius: 12))
                    .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Palette.line, lineWidth: 0.5))

                SecureField("密码", text: $password)
                    .textContentType(.password)
                    .padding(12)
                    .background(Palette.card, in: RoundedRectangle(cornerRadius: 12))
                    .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Palette.line, lineWidth: 0.5))

                if let notice = store.errorNotice {
                    Text(notice).font(.captionText).foregroundStyle(Palette.red)
                }

                PrimaryButton(title: busy ? "正在登录…" : "登录", enabled: !busy) {
                    busy = true
                    Task {
                        let ok = await store.login(email: email, password: password)
                        busy = false
                        if ok { dismiss() }
                    }
                }
                Spacer()
            }
            .padding(20)
            .background(Palette.warm)
            .navigationTitle("连接服务器")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("取消") { dismiss() }
                }
            }
        }
    }
}

import SwiftUI
import PhotosUI

@main
struct MTGLoggerApp: App {
    @StateObject private var model = AppModel()
    var body: some Scene {
        WindowGroup {
            RootView().environmentObject(model)
                .tint(Color(red: 0.91, green: 0.25, blue: 0.34))
                .preferredColorScheme(.dark)
        }
    }
}

struct RootView: View {
    @EnvironmentObject private var model: AppModel
    @State private var selection = 0
    var body: some View {
        if !model.configured {
            NavigationStack { ConnectionView() }
        } else {
            TabView(selection: $selection) {
                NavigationStack { ScannerView(active: selection == 0) }
                    .tabItem { Label("Scan", systemImage: "viewfinder") }.tag(0)
                if let server = model.server {
                    WebPortal(server: server, page: "collection", refreshID: model.refreshID)
                        .tabItem { Label("Collection", systemImage: "rectangle.stack") }.tag(1)
                    WebPortal(server: server, page: "review", refreshID: model.refreshID)
                        .tabItem { Label("Review", systemImage: "checklist") }.tag(2)
                    WebPortal(server: server, page: "dashboard", refreshID: model.refreshID)
                        .tabItem { Label("Library", systemImage: "square.grid.2x2") }.tag(3)
                }
                NavigationStack { ConnectionView() }
                    .tabItem { Label("Settings", systemImage: "gearshape") }.tag(4)
            }
        }
    }
}

struct ConnectionView: View {
    @EnvironmentObject private var model: AppModel
    @State private var address = ""
    var body: some View {
        Form {
            Section {
                VStack(alignment: .leading, spacing: 10) {
                    Image("AppMark").resizable().scaledToFit().frame(height: 80)
                    Text("Your collection. Your camera.").font(.title2.bold())
                    Text("Connect to your MTGLogger server to scan, review, and organize cards directly from your iPhone.")
                        .foregroundStyle(.secondary)
                }.padding(.vertical, 12)
            }
            Section("MTGLogger server") {
                TextField("http://192.168.1.254:5173", text: $address)
                    .textInputAutocapitalization(.never).autocorrectionDisabled().keyboardType(.URL)
                Button {
                    Task { await model.connect(to: address) }
                } label: {
                    HStack { Text(model.checking ? "Connecting…" : "Connect to server"); Spacer(); if model.checking { ProgressView() } }
                }.disabled(model.checking || model.uploading)
                Text("Use your home Wi-Fi for a local address. For access away from home, use your existing secure connection to the server.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
            if let error = model.error { Section { Text(error).foregroundStyle(.orange) } }
            if !model.pending.isEmpty {
                Section("Saved on this iPhone") {
                    Text("\(model.pending.count) capture(s) waiting for the server. Each keeps its original server and batch settings.")
                    Button("Retry saved scans") { Task { await model.retryPending() } }.disabled(model.uploading)
                }
            }
            Section {
                Text("MTGLogger \(Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "") · Camera scans go to your own server. Library opens the full website, including decks, value, database, play, and exports.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
        }
        .navigationTitle("MTGLogger")
        .onAppear { address = model.serverText }
    }
}

struct ScannerView: View {
    let active: Bool
    @EnvironmentObject private var model: AppModel
    @Environment(\.scenePhase) private var scenePhase
    @StateObject private var camera = CardCamera()
    @State private var automatic = true
    @State private var showDefaults = false
    @State private var photo: PhotosPickerItem?

    private var shouldRun: Bool { active && scenePhase == .active && !showDefaults }
    var body: some View {
        ScrollView {
            VStack(spacing: 14) {
                ZStack(alignment: .bottom) {
                    CameraPreview(session: camera.session, onFocus: camera.focus).background(.black)
                    RoundedRectangle(cornerRadius: 14).strokeBorder(.white.opacity(0.65), style: StrokeStyle(lineWidth: 2, dash: [18, 10]))
                        .aspectRatio(63.0 / 88, contentMode: .fit).padding(22).padding(.bottom, 44)
                        .allowsHitTesting(false)
                    Text(camera.message).font(.subheadline.weight(.semibold)).multilineTextAlignment(.center)
                        .padding(12).frame(maxWidth: .infinity).background(.ultraThinMaterial)
                        .allowsHitTesting(false)
                }
                .frame(height: min(UIScreen.main.bounds.height * 0.47, 460)).clipShape(RoundedRectangle(cornerRadius: 20))
                .accessibilityLabel("Rear camera card preview")
                HStack(spacing: 12) {
                    if camera.lenses.count > 1 {
                        Picker("Camera lens", selection: Binding(get: { camera.lens }, set: camera.selectLens)) {
                            ForEach(camera.lenses) { lens in Text(lens.rawValue).tag(lens) }
                        }.pickerStyle(.menu).fixedSize()
                    } else { Image(systemName: "plus.magnifyingglass").foregroundStyle(.secondary) }
                    Slider(value: Binding(get: { camera.zoom }, set: camera.setZoom),
                           in: 1...max(1.01, camera.maximumZoom), step: 0.1)
                        .accessibilityLabel("Camera zoom")
                        .disabled(!camera.running || camera.maximumZoom <= 1)
                    Text(String(format: "%.1f×", camera.zoom)).font(.subheadline.monospacedDigit()).frame(width: 44)
                    Button { camera.resetZoom() } label: { Image(systemName: "arrow.counterclockwise") }
                        .accessibilityLabel("Use suggested camera zoom")
                }
                Text(camera.cameraHint).font(.caption).foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                HStack {
                    PhotosPicker(selection: $photo, matching: .images) { Image(systemName: "photo").font(.title2).frame(width: 54, height: 54) }
                        .disabled(!model.canCapture).accessibilityLabel("Scan a saved card photo")
                    Spacer()
                    Button { camera.capture() } label: {
                        Image(systemName: "camera.aperture").font(.system(size: 44)).frame(width: 76, height: 64)
                    }.buttonStyle(.borderedProminent).clipShape(Capsule())
                        .disabled(!camera.running || !model.canCapture).accessibilityLabel("Capture this physical card")
                    Spacer()
                    Button { camera.toggleTorch() } label: {
                        Image(systemName: camera.torch ? "flashlight.on.fill" : "flashlight.off.fill").font(.title2).frame(width: 54, height: 54)
                    }.accessibilityLabel("Toggle camera light")
                }
                Toggle("Capture automatically when clear and steady", isOn: $automatic)
                    .onChange(of: automatic) { _, value in camera.setAutomatic(value) }
                HStack {
                    Label("\(model.added) added", systemImage: "checkmark.circle.fill").foregroundStyle(.green)
                    Spacer()
                    Label("\(model.reviewed) review", systemImage: "checklist").foregroundStyle(.orange)
                    if model.uploading { ProgressView() }
                }.font(.subheadline)
                if let result = model.result { ScanReceiptView(result: result) }
                if !model.pending.isEmpty {
                    HStack {
                        Label("\(model.pending.count) saved on iPhone", systemImage: "tray.and.arrow.up")
                        Spacer()
                        Button("Retry") { Task { await model.retryPending() } }.disabled(model.uploading)
                    }.font(.subheadline).padding().background(.thinMaterial, in: RoundedRectangle(cornerRadius: 14))
                }
                if let error = camera.error ?? model.error {
                    Text(error).font(.footnote).foregroundStyle(.orange).frame(maxWidth: .infinity, alignment: .leading)
                }
                Text("Keep the full card visible and tilt away from glare. Remove each captured card before presenting the next. The shutter also lets you deliberately scan another physical copy.")
                    .font(.footnote).foregroundStyle(.secondary)
            }.padding(.horizontal).padding(.bottom, 16)
        }
        .background(Color(red: 0.065, green: 0.035, blue: 0.04))
        .navigationTitle("Scanner").navigationBarTitleDisplayMode(.inline)
        .toolbar { ToolbarItem(placement: .topBarTrailing) { Button { showDefaults = true } label: { Image(systemName: "slider.horizontal.3") }.accessibilityLabel("Batch defaults") } }
        .sheet(isPresented: $showDefaults) { NavigationStack { BatchDefaultsView() } }
        .onAppear {
            camera.onCapture = { jpeg in Task { await model.capture(jpeg); camera.setReady(model.canCapture) } }
            camera.setReady(model.canCapture)
            if shouldRun { camera.start() }
        }
        .onDisappear { camera.stop() }
        .onChange(of: shouldRun) { _, run in if run { camera.start() } else { camera.stop() } }
        .onChange(of: model.canCapture) { _, ready in camera.setReady(ready) }
        .onChange(of: photo) { _, item in
            guard let item else { return }
            Task {
                do {
                    guard let data = try await item.loadTransferable(type: Data.self),
                          let image = UIImage(data: data), let jpeg = CardCamera.jpeg(image) else {
                        throw APIError("This photo could not be opened. Choose another image or use the camera.")
                    }
                    await model.capture(jpeg)
                } catch { model.error = error.localizedDescription }
                photo = nil
            }
        }
    }
}

struct ScanReceiptView: View {
    let result: ScanResult
    var body: some View {
        HStack(spacing: 14) {
            if let url = result.imageURL {
                AsyncImage(url: url) { image in image.resizable().scaledToFit() } placeholder: { ProgressView() }
                    .frame(width: 64, height: 90).clipShape(RoundedRectangle(cornerRadius: 5))
            }
            VStack(alignment: .leading, spacing: 5) {
                Text(result.disposition == "added" ? "ADDED TO COLLECTION" : result.disposition == "empty" ? "NO CARD DETECTED" : "SAVED FOR REVIEW")
                    .font(.caption.bold()).foregroundStyle(result.disposition == "added" ? .green : .orange)
                Text(result.name).font(.headline)
                Text(result.printing).font(.subheadline).foregroundStyle(.secondary)
                Text(String(format: "%.1f%% confidence · %.1fs", result.confidence, Double(result.processing_ms) / 1000))
                    .font(.caption).foregroundStyle(.secondary)
            }
            Spacer(minLength: 0)
        }.padding().frame(maxWidth: .infinity).background(.thinMaterial, in: RoundedRectangle(cornerRadius: 16))
    }
}

struct BatchDefaultsView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        Form {
            Section("Physical copies") {
                Picker("Condition", selection: $model.defaults.condition) {
                    Text("Near Mint").tag("near_mint"); Text("Lightly Played").tag("lightly_played")
                    Text("Moderately Played").tag("moderately_played"); Text("Heavily Played").tag("heavily_played"); Text("Damaged").tag("damaged")
                }
                Toggle("Foil", isOn: $model.defaults.foil)
                Picker("Language", selection: $model.defaults.language) {
                    ForEach(["en", "es", "fr", "de", "it", "pt", "ja", "ko", "ru", "zhs", "zht", "he", "la", "grc", "ar", "sa", "phy"], id: \.self) { code in
                        Text(Locale.current.localizedString(forLanguageCode: code) ?? code).tag(code)
                    }
                }
                TextField("Storage location", text: $model.defaults.storage_location)
                TextField("Collection", text: $model.defaults.collection_name)
            }
            Section("Recognition") {
                Toggle("Automatically add certain matches", isOn: $model.defaults.auto_add)
                TextField("Box set code (optional)", text: Binding(
                    get: { model.defaults.box_set_code ?? "" },
                    set: { model.defaults.box_set_code = $0.isEmpty ? nil : $0.lowercased() }
                )).textInputAutocapitalization(.never).autocorrectionDisabled()
                Text("Uncertain printings always retain their scan in Review. Set a box code only when every card belongs to that set.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
            Section("Add to deck") {
                Picker("Deck", selection: Binding(get: { model.defaults.deck_id ?? "" }, set: { model.defaults.deck_id = $0.isEmpty ? nil : $0 })) {
                    Text("No deck").tag("")
                    ForEach(model.decks) { deck in Text(deck.name).tag(deck.id) }
                }
            }
        }.navigationTitle("Batch defaults").navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
            .task { if let server = model.server { model.decks = (try? await MTGAPI(server: server).decks()) ?? model.decks } }
    }
}

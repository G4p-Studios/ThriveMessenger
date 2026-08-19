import AVFoundation

@MainActor final class VoiceAudioService {
    var onCaptured: ((Data) -> Void)?
    private let engine = AVAudioEngine(), player = AVAudioPlayerNode()
    private let format = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16_000, channels: 1, interleaved: true)!
    func start() async throws {
        let session = AVAudioSession.sharedInstance(); try session.setCategory(.playAndRecord, mode: .voiceChat, options: [.defaultToSpeaker, .allowBluetoothHFP]); try session.setActive(true)
        if !engine.attachedNodes.contains(player) { engine.attach(player) }; engine.connect(player, to: engine.mainMixerNode, format: format)
        let input = engine.inputNode, inputFormat = input.outputFormat(forBus: 0), converter = AVAudioConverter(from: inputFormat, to: format)!
        input.installTap(onBus: 0, bufferSize: 960, format: inputFormat) { [weak self] source, _ in guard let self, let output = AVAudioPCMBuffer(pcmFormat: self.format, frameCapacity: 960) else { return }; var used = false; converter.convert(to: output, error: nil) { _, status in if used { status.pointee = .noDataNow; return nil }; used = true; status.pointee = .haveData; return source }; guard let samples = output.int16ChannelData else { return }; let data = Data(bytes: samples[0], count: Int(output.frameLength) * 2); Task { @MainActor in self.onCaptured?(data) } }
        engine.prepare(); try engine.start(); player.play()
    }
    func play(_ data: Data, deafened: Bool) { guard !deafened, let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(data.count / 2)) else { return }; buffer.frameLength = buffer.frameCapacity; data.withUnsafeBytes { if let source = $0.baseAddress, let destination = buffer.int16ChannelData?[0] { destination.update(from: source.assumingMemoryBound(to: Int16.self), count: data.count / 2) } }; player.scheduleBuffer(buffer) }
    func stop() { if engine.isRunning { engine.inputNode.removeTap(onBus: 0) }; player.stop(); engine.stop(); try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation) }
}
enum SoundPlayer { private static var player: AVAudioPlayer?; static func play(_ name: String, loops: Int = 0) { stop(); guard let url = Bundle.main.url(forResource: name, withExtension: "wav") else { return }; player = try? AVAudioPlayer(contentsOf: url); player?.numberOfLoops = loops; player?.play() }; static func stop() { player?.stop(); player = nil } }

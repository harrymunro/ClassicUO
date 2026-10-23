// Records several windows (each found by its owning process id) to .mov files with ScreenCaptureKit,
// from one process: separate recorder processes interrupt each other's streams (error -3805) or hang.
// Each window is recorded whether or not other windows cover it. Built by build.sh; film.py runs it.
//
//   wincaps [--seconds N] [--stop FILE] [--fps 30] PID OUT.mov [PID OUT.mov ...]
//   wincaps --displays     the number of displays it can record: 0 while the screen is locked (cuo-cym.32)
//
// Stops after N seconds, when FILE exists, or on SIGINT/SIGTERM. Writes OUT.mov.start (epoch seconds)
// when each recording starts. A .mov can't be read until it is closed. Needs macOS 15 and the Screen
// Recording permission for the terminal it runs from.
import Foundation
import ScreenCaptureKit
import AVFoundation
import CoreMedia
import AppKit

_ = NSApplication.shared
_ = CGMainDisplayID()

var stopRequested = false
signal(SIGINT) { _ in stopRequested = true }
signal(SIGTERM) { _ in stopRequested = true }

final class Delegate: NSObject, SCStreamDelegate, SCRecordingOutputDelegate {
    let path: String
    var finished = false
    init(_ p: String) { path = p }
    func stream(_ stream: SCStream, didStopWithError error: Error) {
        fputs("wincaps \(path): stream stopped: \(error)\n", stderr); finished = true
    }
    func recordingOutputDidStartRecording(_ o: SCRecordingOutput) {
        try? String(format: "%.3f\n", Date().timeIntervalSince1970).write(toFile: path + ".start", atomically: true, encoding: .utf8)
        fputs("wincaps \(path): recording\n", stderr)
    }
    func recordingOutput(_ o: SCRecordingOutput, didFailWithError error: Error) {
        fputs("wincaps \(path): recording failed: \(error)\n", stderr); finished = true
    }
    func recordingOutputDidFinishRecording(_ o: SCRecordingOutput) { fputs("wincaps \(path): finished\n", stderr); finished = true }
}

final class Sink: NSObject, SCStreamOutput {
    func stream(_ s: SCStream, didOutputSampleBuffer b: CMSampleBuffer, of t: SCStreamOutputType) {}
}

var seconds = 3600.0, stopFile: String? = nil, fps = 30
var pairs: [(Int32, String)] = []
let args = Array(CommandLine.arguments.dropFirst())
var i = 0
while i < args.count {
    switch args[i] {
    case "--seconds": seconds = Double(args[i + 1])!; i += 2
    case "--stop": stopFile = args[i + 1]; i += 2
    case "--fps": fps = Int(args[i + 1])!; i += 2
    case "--displays": i += 1
    default:
        guard i + 1 < args.count, let pid = Int32(args[i]) else { fputs("usage: wincaps [--seconds N] [--stop FILE] [--fps 30] PID OUT.mov ...\n", stderr); exit(2) }
        pairs.append((pid, args[i + 1])); i += 2
    }
}
guard !pairs.isEmpty || args.first == "--displays" else { fputs("wincaps: nothing to record\n", stderr); exit(2) }

let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
if CommandLine.arguments.dropFirst().first == "--displays" { print(content.displays.count); exit(0) }
var streams: [SCStream] = [], delegates: [Delegate] = [], keep: [AnyObject] = []
for (pid, path) in pairs {
    let candidates = content.windows.filter { $0.owningApplication?.processID == pid && $0.frame.width > 200 }
    guard let win = candidates.max(by: { $0.frame.width * $0.frame.height < $1.frame.width * $1.frame.height }) else {
        fputs("wincaps: no window for pid \(pid)\n", stderr); continue
    }
    let filter = SCContentFilter(desktopIndependentWindow: win)
    let scale = CGFloat(filter.pointPixelScale)
    let cfg = SCStreamConfiguration()
    cfg.width = Int(win.frame.width * scale)
    cfg.height = Int(win.frame.height * scale)
    cfg.minimumFrameInterval = CMTime(value: 1, timescale: CMTimeScale(fps))
    cfg.showsCursor = false
    cfg.queueDepth = 8
    cfg.ignoreShadowsSingleWindow = true
    cfg.capturesAudio = false
    fputs("wincaps \(path): window '\(win.title ?? "")' \(win.frame) -> \(cfg.width)x\(cfg.height)\n", stderr)
    let delegate = Delegate(path)
    let stream = SCStream(filter: filter, configuration: cfg, delegate: delegate)
    let sink = Sink()
    try stream.addStreamOutput(sink, type: .screen, sampleHandlerQueue: DispatchQueue(label: "sink.\(pid)"))
    let rc = SCRecordingOutputConfiguration()
    rc.outputURL = URL(fileURLWithPath: path)
    rc.outputFileType = .mov
    rc.videoCodecType = .h264
    let recording = SCRecordingOutput(configuration: rc, delegate: delegate)
    try stream.addRecordingOutput(recording)
    try await stream.startCapture()
    streams.append(stream); delegates.append(delegate); keep.append(sink); keep.append(recording)
}
guard !streams.isEmpty else { exit(1) }

let start = Date()
while !stopRequested && Date().timeIntervalSince(start) < seconds && !delegates.allSatisfy({ $0.finished }) {
    if let f = stopFile, FileManager.default.fileExists(atPath: f) { break }
    try await Task.sleep(nanoseconds: 200_000_000)
}
for s in streams { try? await s.stopCapture() }
let deadline = Date().addingTimeInterval(20)
while !delegates.allSatisfy({ $0.finished }) && Date() < deadline { try await Task.sleep(nanoseconds: 100_000_000) }
fputs("wincaps: \(Int(Date().timeIntervalSince(start))) s\n", stderr)

// Copyright 2026 PAI inspection contributors. Apache-2.0.
#pragma once
#include <chrono>
#include <optional>
namespace pai::sim {
// Owner synchronizes callbacks with the simulator update thread.
class CommandLease {
 public:
  using Clock = std::chrono::steady_clock;
  void Receive(Clock::time_point now = Clock::now()) { last = now; }
  void Invalidate() { last.reset(); }
  bool Expired(Clock::time_point now = Clock::now()) const {
    return !last || now - *last >= std::chrono::milliseconds(500);
  }
 private:
  std::optional<Clock::time_point> last;
};
}

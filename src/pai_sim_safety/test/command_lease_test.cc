#include "pai_sim_safety/CommandLease.hh"
#include <stdexcept>
static void Require(bool condition) {
  if (!condition) throw std::runtime_error("command lease safety regression");
}
int main() {
  using namespace std::chrono;
  pai::sim::CommandLease lease;
  auto t = pai::sim::CommandLease::Clock::time_point{};
  Require(lease.Expired(t));
  lease.Receive(t);
  Require(!lease.Expired(t + milliseconds(499)));
  Require(lease.Expired(t + milliseconds(500)));
  // Pausing simulator time cannot keep a wall-clock command alive.
  Require(lease.Expired(t + seconds(10)));
  lease.Receive(t + seconds(10));
  Require(!lease.Expired(t + seconds(10)));
  lease.Invalidate();
  Require(lease.Expired(t + seconds(10)));
}

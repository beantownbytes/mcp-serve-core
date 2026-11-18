//! Shared native-MCP serving core for tool servers.

pub mod catalog;
pub mod config;
pub mod deadline;
pub mod result;
pub mod server;
pub mod spec;
#[cfg(feature = "testing")]
pub mod testing;

pub use rmcp;

use mcp_serve_core::result::{CallOutcome, into_result};
use serde_json::Value;
use std::time::Duration;

const FIXTURE: &str = concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../testdata/golden_results.json"
);

fn cases() -> Vec<Value> {
    let raw = std::fs::read_to_string(FIXTURE).expect("read the golden fixture");
    match serde_json::from_str(&raw).expect("parse the golden fixture") {
        Value::Array(cases) => cases,
        other => panic!("the golden fixture must be an array, got {other}"),
    }
}

fn string_arg(case: &Value) -> String {
    case["args"]
        .as_str()
        .expect("args must be a string")
        .to_string()
}

fn outcome(case: &Value) -> CallOutcome {
    match case["outcome"].as_str().expect("outcome must be a string") {
        "UnknownTool" => CallOutcome::UnknownTool(string_arg(case)),
        "TimedOut" => CallOutcome::TimedOut(Duration::from_secs(
            case["args"].as_u64().expect("args must be whole seconds"),
        )),
        "Canceled" => CallOutcome::Canceled,
        "Panicked" => CallOutcome::Panicked,
        "Success" => CallOutcome::Success(string_arg(case)),
        "Policy" => CallOutcome::Policy(string_arg(case)),
        "Tool" => CallOutcome::Tool(string_arg(case)),
        "Config" => CallOutcome::Config(string_arg(case)),
        other => panic!("unknown outcome {other:?} in the golden fixture"),
    }
}

#[test]
fn every_golden_case_maps_to_its_expected_result() {
    let cases = cases();
    assert!(!cases.is_empty(), "the golden fixture has no cases");
    for case in &cases {
        let name = case["name"].as_str().expect("name must be a string");
        let cap = case["cap"].as_u64().expect("cap must be a number") as usize;
        let result = into_result(outcome(case), cap);
        let text: String = result
            .content
            .iter()
            .filter_map(|c| c.as_text().map(|t| t.text.clone()))
            .collect();
        assert_eq!(
            text,
            case["expect"]["text"].as_str().unwrap(),
            "{name}: text"
        );
        assert_eq!(
            result.is_error,
            Some(case["expect"]["isError"].as_bool().unwrap()),
            "{name}: isError"
        );
    }
}

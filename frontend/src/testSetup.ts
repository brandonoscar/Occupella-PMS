// Before every test file: the company's day is UTC's (src/money.ts), as the tests count today
// with `new Date()`, so a run between midnight UTC and midnight in New York agrees with itself.
import { setTimeZone } from "./money";

setTimeZone("UTC");

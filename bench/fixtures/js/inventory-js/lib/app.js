import { top } from "..";
import { format } from "./format";
import { Badge } from "./badge";

export function helper() {
  return top();
}

export function create() {
  return format(helper());
}

export default function render() {
  return Badge();
}

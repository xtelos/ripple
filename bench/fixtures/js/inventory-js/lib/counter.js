import { format } from "./format.js";

export class Counter {
  #count = 0;
  static #made = 0;

  static {
    Counter.#made = 0;
  }

  static make() {
    Counter.#made += 1;
    return new Counter();
  }

  #bump() {
    this.#count += 1;
    return format(this.#count);
  }

  get label() {
    return this.#bump();
  }

  increment() {
    return this.#bump();
  }

  async load(source) {
    return this.increment() + (await source());
  }

  *items() {
    yield this.increment();
  }
}

export function build() {
  return Counter.make();
}

export function read() {
  const counter = new Counter();
  return counter.label;
}

package com.acme.shop;

import java.io.IOException;
import java.io.Writer;

/** Extends a JDK class that declares write(String): a call of `write` cannot be proven to be the repo's. */
public class Ledger extends Writer {
    private final StringBuilder out = new StringBuilder();

    public void write(Item item) {
        out.append(item.name());
    }

    @Override
    public void write(char[] buf, int off, int len) {
        out.append(buf, off, len);
    }

    @Override
    public void flush() {}

    @Override
    public void close() {}

    public void record(Item item) throws IOException {
        write(item);
        write("sep");
        this.write(item.name());
    }

    public static void note(Ledger ledger, Item item) throws IOException {
        ledger.record(item);
    }
}

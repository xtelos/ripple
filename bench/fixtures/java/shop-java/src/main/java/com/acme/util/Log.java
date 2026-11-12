package com.acme.util;

import java.util.function.Consumer;

public class Log {
    private final String name;
    private final Consumer<String> sink = System.out::println;

    public Log(String name) {
        this.name = name;
    }

    public void info(String msg) {
        sink.accept(name + ": " + msg);
    }

    public void info(String fmt, Object... args) {
        info(String.format(fmt, args));
    }
}

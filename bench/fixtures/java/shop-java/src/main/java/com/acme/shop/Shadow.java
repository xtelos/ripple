package com.acme.shop;

class Format {
    static String print() {
        return "f";
    }
}

class Printer {
    String print() {
        return "p";
    }
}

public class Shadow {
    String variable() {
        Printer Format = new Printer();
        return Format.print();
    }

    String type() {
        return Format.print();
    }
}

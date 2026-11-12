package com.acme.shop;

import java.util.List;

public class Reports {
    public static int sum(List<Priced> all) {
        int sum = 0;
        for (Priced p : all) {
            sum += p.total();
        }
        return sum;
    }

    public static void labels(List<Priced> all) {
        all.forEach(p -> p.label());
        all.forEach((Priced q) -> q.label());
    }

    static class Row {
        String text() {
            return "row";
        }
    }

    public static void run() {
        Runnable r = new Runnable() {
            @Override
            public void run() {
                helper();
            }
        };
        r.run();
        Priced fixed = new Priced() {
            public int total() {
                return 7;
            }
        };
        fixed.total();
        new Cart().total();
        Cart c = new BulkCart(2);
        c.total();
        new Row().text();
    }

    static void helper() {}
}

package com.acme.shop;

import com.acme.util.*;
import java.util.ArrayList;
import java.util.List;
import static com.acme.util.Money.format;
import static com.acme.util.Strings.*;
import static java.lang.Math.max;

public class Cart implements Priced {
    protected final List<Item> items = new ArrayList<>();
    private final Log log = new Log("cart");

    public Cart() {
        this(new ArrayList<>());
    }

    public Cart(List<Item> initial) {
        items.addAll(initial);
    }

    public void add(Item item) {
        items.add(item);
        log.info("added " + item.name());
        log.info("added %s", item);
    }

    public void add(String name) {
        add(Item.free(name));
    }

    public void add(String name, int cents) {
        add(new Item(name, cents));
    }

    @Override
    public int total() {
        int sum = 0;
        for (Item i : items) {
            sum += i.cents();
        }
        return sum;
    }

    @Override
    public String label() {
        return Priced.super.label() + "!";
    }

    public String receipt() {
        var first = items.isEmpty() ? null : items.get(0);
        String head = first == null ? "" : first.name();
        return head + format(total()) + label();
    }

    public final String id() {
        return "cart";
    }

    public String loud() {
        return shout(receipt()) + max(1, 2);
    }

    public Cart copy() {
        Cart c = new Cart();
        c.add("copy");
        return c;
    }

    public int each() {
        items.forEach(this::show);
        class Counter {
            int count(List<Item> all) {
                return all.size();
            }
        }
        return new Counter().count(items);
    }

    private void show(Item item) {
        log.info(item.name());
    }

    class Line {
        String show() {
            return Cart.this.receipt() + receipt();
        }
    }
}

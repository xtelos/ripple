import java.io.BufferedReader;
import java.io.FileReader;
import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.util.ArrayDeque;
import java.util.HashSet;
import java.util.Set;

/**
 * What the running JDK's own reflection says about each type named on a line of args[0] (a canonical name), for
 * bench/java_jdk/reflect.py to hold against ripple's table. It shares nothing with the table generator: not its class file
 * reader, not its merge. Java 8 source, compiled and run by each JDK in turn.
 *
 * One line per fact, tab separated:
 *   NAME ABSENT                the JDK cannot load the type, or loads one that is not named NAME, or one a repo cannot name
 *                              (not public, or a member type that is not public or protected, or inside a type that is not)
 *   NAME PRESENT final|open
 *   NAME S supertype           a public or protected type above NAME, by canonical name
 *   NAME M method n v|-        a public or protected method NAME declares or inherits: n parameters, v when varargs
 *   NAME F field
 *   NAME T membertype          a public or protected member type NAME declares or inherits (simple name)
 *   NAME C                     NAME itself declares a public or protected constructor (a constructor is not inherited)
 */
public class Reflect {
    static Class<?> find(String canonical) {
        String name = canonical;
        while (true) {
            try {
                return Class.forName(name, false, ClassLoader.getSystemClassLoader());
            } catch (Throwable e) {
                int dot = name.lastIndexOf('.');
                if (dot < 0) return null;
                name = name.substring(0, dot) + "$" + name.substring(dot + 1);
            }
        }
    }

    static boolean visible(int mods) {
        return Modifier.isPublic(mods) || Modifier.isProtected(mods);
    }

    /** A type a repo can name: a top-level one is public, a member one is public or protected and so is everything around it.
     *  (java.lang.foreign.PaddingLayout is package-private in JDK 19 and public from 20: in 19 it is ABSENT.) */
    static boolean nameable(Class<?> c) {
        Class<?> outer = c.getDeclaringClass();
        return outer == null ? Modifier.isPublic(c.getModifiers()) : visible(c.getModifiers()) && nameable(outer);
    }

    public static void main(String[] args) throws Exception {
        BufferedReader in = new BufferedReader(new FileReader(args[0]));
        String canonical;
        while ((canonical = in.readLine()) != null) {
            Class<?> c = find(canonical);
            if (c == null || !canonical.equals(c.getCanonicalName()) || !nameable(c)) {
                System.out.println(canonical + "\tABSENT");
                continue;
            }
            System.out.println(canonical + "\tPRESENT\t" + (Modifier.isFinal(c.getModifiers()) ? "final" : "open"));
            for (Constructor<?> k : c.getDeclaredConstructors())
                if (visible(k.getModifiers())) {
                    System.out.println(canonical + "\tC");
                    break;
                }
            Set<Class<?>> seen = new HashSet<Class<?>>();
            ArrayDeque<Class<?>> work = new ArrayDeque<Class<?>>();
            work.add(c);
            while (!work.isEmpty()) {
                Class<?> k = work.poll();
                if (k == null || !seen.add(k)) continue;
                if (k.getSuperclass() != null) work.add(k.getSuperclass());
                for (Class<?> i : k.getInterfaces()) work.add(i);
                if (k != c && k != Object.class && k.getCanonicalName() != null && visible(k.getModifiers()))
                    System.out.println(canonical + "\tS\t" + k.getCanonicalName());
                if (k == Object.class) continue;
                for (Method m : k.getDeclaredMethods())
                    if (visible(m.getModifiers()))
                        System.out.println(canonical + "\tM\t" + m.getName() + "\t" + m.getParameterTypes().length + "\t" + (m.isVarArgs() ? "v" : "-"));
                for (Field f : k.getDeclaredFields())
                    if (visible(f.getModifiers()))
                        System.out.println(canonical + "\tF\t" + f.getName());
                for (Class<?> t : k.getDeclaredClasses())
                    if (visible(t.getModifiers()))
                        System.out.println(canonical + "\tT\t" + t.getSimpleName());
            }
        }
    }
}

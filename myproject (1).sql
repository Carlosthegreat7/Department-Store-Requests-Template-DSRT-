-- phpMyAdmin SQL Dump
-- version 5.2.1
-- https://www.phpmyadmin.net/
--
-- Host: 127.0.0.1
-- Generation Time: Jan 16, 2026 at 06:13 AM
-- Server version: 10.4.32-MariaDB
-- PHP Version: 8.2.12

SET SQL_MODE = "NO_AUTO_VALUE_ON_ZERO";
START TRANSACTION;
SET time_zone = "+00:00";


/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!40101 SET NAMES utf8mb4 */;

--
-- Database: `myproject`
--

-- --------------------------------------------------------

--
-- Table structure for table `brands`
--

CREATE TABLE `brands` (
  `product_group` varchar(20) NOT NULL,
  `brand_name` varchar(100) NOT NULL,
  `dept_code` varchar(10) NOT NULL,
  `sub_dept_code` varchar(10) NOT NULL,
  `class_code` varchar(10) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

--
-- Dumping data for table `brands`
--

INSERT INTO `brands` (`product_group`, `brand_name`, `dept_code`, `sub_dept_code`, `class_code`) VALUES
('ADIDA', 'ADIDAS', '054', '092', '279'),
('ANNEK', 'ANNE KLEIN', '054', '092', '262'),
('AXIS', 'AXIS', '054', '092', '125'),
('DKNY', 'DKNY', '054', '092', '325'),
('ESPRIT', 'ESPRIT', '054', '092', '126'),
('FERRA', 'FERRAGAMO', '054', '092', '234'),
('GIORD', 'GIORDANO', '054', '092', '122'),
('GUESS', 'GUESS', '054', '092', '297'),
('GUESSJ', 'GUESS JEWELRY', '054', '091', '322'),
('INGER', 'INGERSOLL', '054', '092', '130'),
('NIXON', 'NIXON', '054', '092', '127'),
('POGI1', 'CARL', '215', '067', '026'),
('POLIS', 'POLICE', '054', '092', '124'),
('SWISS', 'SWISS MILITARY', '054', '092', '257'),
('TIMEX', 'TIMEX', '054', '092', '121'),
('TITAN', 'TITAN', '054', '092', '229'),
('VERSA', 'VERSACE', '054', '092', '235');

-- --------------------------------------------------------

--
-- Table structure for table `products`
--

CREATE TABLE `products` (
  `id` int(11) NOT NULL,
  `description` text NOT NULL,
  `color` varchar(50) DEFAULT NULL,
  `sizes` varchar(100) DEFAULT NULL,
  `style_stockcode` varchar(100) NOT NULL,
  `source_marked` varchar(100) DEFAULT NULL,
  `srp` decimal(10,2) NOT NULL,
  `unit_of_measure` varchar(50) NOT NULL,
  `exp_del_month` varchar(20) NOT NULL,
  `remarks` text DEFAULT NULL,
  `pricepoint_sku` varchar(100) DEFAULT NULL,
  `images` varchar(255) NOT NULL,
  `online_items` varchar(50) NOT NULL,
  `package_length_cm` decimal(10,2) NOT NULL,
  `package_width_cm` decimal(10,2) NOT NULL,
  `package_height_cm` decimal(10,2) NOT NULL,
  `package_weight_kg` decimal(10,2) NOT NULL,
  `product_length_cm` decimal(10,2) NOT NULL,
  `product_width_cm` decimal(10,2) NOT NULL,
  `product_height_cm` decimal(10,2) NOT NULL,
  `product_weight_kg` decimal(10,2) NOT NULL,
  `created_at` timestamp NOT NULL DEFAULT current_timestamp(),
  `product_group` varchar(20) DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `sub_classes`
--

CREATE TABLE `sub_classes` (
  `product_group` varchar(20) NOT NULL,
  `subclass_code` varchar(10) NOT NULL,
  `subclass_name` varchar(100) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

--
-- Dumping data for table `sub_classes`
--

INSERT INTO `sub_classes` (`product_group`, `subclass_code`, `subclass_name`) VALUES
('ADIDA', '013', 'LEATHER'),
('ADIDA', '014', 'STAINLESS'),
('ADIDA', '015', 'PLASTICK'),
('ADIDA', '016', 'RUBBER'),
('ADIDA', '017', 'SETS'),
('ADIDA', '100', 'MD PROMO'),
('ANNEK', '013', 'LEATHER'),
('ANNEK', '014', 'STAINLESS'),
('ANNEK', '015', 'PLASTICK'),
('ANNEK', '016', 'RUBBER'),
('ANNEK', '017', 'SETS'),
('ANNEK', '018', 'STRAP'),
('ANNEK', '100', 'MD PROMO'),
('AXIS', '013', 'LEATHER'),
('AXIS', '014', 'STAINLESS'),
('AXIS', '015', 'PLASTICK'),
('AXIS', '016', 'RUBBER'),
('AXIS', '100', 'MD PROMO'),
('DKNY', '013', 'LEATHER'),
('DKNY', '014', 'STAINLESS'),
('DKNY', '016', 'RUBBER'),
('DKNY', '100', 'MD PROMO'),
('ESPRT', '013', 'LEATHER'),
('ESPRT', '014', 'STAINLESS'),
('ESPRT', '015', 'PLASTICK'),
('ESPRT', '016', 'RUBBER'),
('ESPRT', '017', 'SETS'),
('ESPRT', '018', 'STRAP'),
('ESPRT', '100', 'MD PROMO'),
('FERRA', '013', 'LEATHER'),
('FERRA', '014', 'STAINLESS'),
('FERRA', '015', 'PLASTICK'),
('FERRA', '016', 'RUBBER'),
('FERRA', '017', 'SETS'),
('FERRA', '018', 'STRAP'),
('FERRA', '100', 'MD PROMO'),
('GIORD', '013', 'LEATHER'),
('GIORD', '014', 'STAINLESS'),
('GIORD', '015', 'PLASTICK'),
('GIORD', '016', 'RUBBER'),
('GIORD', '100', 'MD PROMO'),
('GUESS', '001', 'LEATHER'),
('GUESS', '002', 'STAINLESS'),
('GUESS', '003', 'RUBBER'),
('GUESS', '100', 'MD PROMO'),
('GUESSJ', '001', 'NECKLACE'),
('GUESSJ', '002', 'EARRINGS'),
('GUESSJ', '003', 'BRACELET'),
('GUESSJ', '004', 'RINGS'),
('GUESSJ', '100', 'SETS'),
('INGER', '013', 'LEATHER'),
('INGER', '014', 'STAINLESS'),
('INGER', '015', 'PLASTICK'),
('INGER', '016', 'RUBBER'),
('INGER', '017', 'SETS'),
('INGER', '018', 'STRAP'),
('INGER', '100', 'MD PROMO'),
('NIXON', '013', 'LEATHER'),
('NIXON', '014', 'STAINLESS'),
('NIXON', '015', 'PLASTICK'),
('NIXON', '016', 'RUBBER'),
('NIXON', '100', 'MD PROMO'),
('POLIS', '013', 'LEATHER'),
('POLIS', '014', 'STAINLESS'),
('POLIS', '015', 'PLASTICK'),
('POLIS', '016', 'RUBBER'),
('POLIS', '100', 'MD PROMO'),
('SOLRAC', '143', 'HEHEHEHE'),
('SWISS', '013', 'LEATHER'),
('SWISS', '014', 'STAINLESS'),
('SWISS', '015', 'PLASTICK'),
('SWISS', '016', 'RUBBER'),
('SWISS', '017', 'SETS'),
('SWISS', '018', 'STRAP'),
('SWISS', '100', 'MD PROMO'),
('TIMEX', '013', 'LEATHER'),
('TIMEX', '014', 'STAINLESS'),
('TIMEX', '015', 'PLASTICK'),
('TIMEX', '016', 'RUBBER'),
('TIMEX', '017', 'SETS'),
('TIMEX', '100', 'MD PROMO'),
('TITAN', '013', 'LEATHER'),
('TITAN', '014', 'STAINLESS'),
('TITAN', '015', 'PLASTICK'),
('TITAN', '016', 'RUBBER'),
('TITAN', '017', 'SETS'),
('TITAN', '018', 'STRAP'),
('TITAN', '100', 'MD PROMO'),
('VERSA', '013', 'LEATHER'),
('VERSA', '014', 'STAINLESS'),
('VERSA', '015', 'PLASTICK'),
('VERSA', '016', 'RUBBER'),
('VERSA', '017', 'SETS'),
('VERSA', '018', 'STRAP'),
('VERSA', '100', 'MD PROMO');

-- --------------------------------------------------------

--
-- Table structure for table `vendors`
--

CREATE TABLE `vendors` (
  `vendor_code` varchar(20) NOT NULL,
  `vendor_name` varchar(255) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

--
-- Dumping data for table `vendors`
--

INSERT INTO `vendors` (`vendor_code`, `vendor_name`) VALUES
('014353', 'ABOUT TIME CORP.'),
('120604', 'CARL CORP.'),
('144011', 'NEWTRENDS INTERNATIONAL CORP.');

--
-- Indexes for dumped tables
--

--
-- Indexes for table `brands`
--
ALTER TABLE `brands`
  ADD PRIMARY KEY (`product_group`);

--
-- Indexes for table `products`
--
ALTER TABLE `products`
  ADD PRIMARY KEY (`id`),
  ADD KEY `fk_brand` (`product_group`);

--
-- Indexes for table `sub_classes`
--
ALTER TABLE `sub_classes`
  ADD PRIMARY KEY (`product_group`,`subclass_code`);

--
-- Indexes for table `vendors`
--
ALTER TABLE `vendors`
  ADD PRIMARY KEY (`vendor_code`);

--
-- AUTO_INCREMENT for dumped tables
--

--
-- AUTO_INCREMENT for table `products`
--
ALTER TABLE `products`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT;

--
-- Constraints for dumped tables
--

--
-- Constraints for table `products`
--
ALTER TABLE `products`
  ADD CONSTRAINT `fk_brand` FOREIGN KEY (`product_group`) REFERENCES `brands` (`product_group`);
COMMIT;

/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
